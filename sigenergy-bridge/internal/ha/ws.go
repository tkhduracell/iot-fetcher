// Package ha implements a minimal Home Assistant WebSocket client that
// surfaces state_changed events for a single configurable entity. Used by
// the controller to react to Wallbox state changes.
package ha

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"math/rand/v2"
	"net/http"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
)

// Event is a filtered state_changed event for the configured entity.
type Event struct {
	EntityID string
	OldState string
	NewState string
}

// Listener is the surface consumed by the controller.
type Listener interface {
	Events() <-chan Event
	// Connected emits true on auth_ok and false on any disconnect. Buffered
	// so a missed read doesn't stall the listener.
	Connected() <-chan bool
}

// Dial creates a Listener and spawns a goroutine that stays connected for
// the lifetime of ctx. Cancelling ctx is the only way to stop it.
func Dial(ctx context.Context, url, token, entityID string, log *slog.Logger) Listener {
	l := &listener{
		url:       url,
		token:     token,
		entityID:  entityID,
		log:       log,
		events:    make(chan Event, 16),
		connected: make(chan bool, 4),
		dialer:    websocket.DefaultDialer,
	}
	go l.runLoop(ctx)
	return l
}

type listener struct {
	url      string
	token    string
	entityID string
	log      *slog.Logger

	events    chan Event
	connected chan bool

	dialer *websocket.Dialer
	msgID  atomic.Int64
}

func (l *listener) Events() <-chan Event    { return l.events }
func (l *listener) Connected() <-chan bool  { return l.connected }

// runLoop reconnects forever with exponential backoff + jitter until ctx is
// cancelled. `auth_invalid` is fatal and stops the loop.
func (l *listener) runLoop(ctx context.Context) {
	backoff := time.Second
	const maxBackoff = 30 * time.Second

	for {
		if ctx.Err() != nil {
			return
		}
		err := l.connectAndServe(ctx)
		if errors.Is(err, errAuthInvalid) {
			l.log.ErrorContext(ctx, "ha auth_invalid; giving up on reconnects", "err", err)
			return
		}
		if ctx.Err() != nil {
			return
		}
		l.notifyConnected(false)
		jitter := time.Duration(rand.Int64N(int64(backoff)))
		sleep := backoff + jitter
		l.log.WarnContext(ctx, "ha ws disconnected, reconnecting", "err", err, "in", sleep)
		select {
		case <-ctx.Done():
			return
		case <-time.After(sleep):
		}
		backoff *= 2
		if backoff > maxBackoff {
			backoff = maxBackoff
		}
	}
}

var errAuthInvalid = errors.New("ha auth_invalid")

type serverMsg struct {
	Type    string          `json:"type"`
	ID      int64           `json:"id,omitempty"`
	Event   *haEvent        `json:"event,omitempty"`
	Message string          `json:"message,omitempty"`
	Success *bool           `json:"success,omitempty"`
	Result  json.RawMessage `json:"result,omitempty"`
}

type haEvent struct {
	EventType string        `json:"event_type"`
	Data      haEventData   `json:"data"`
	TimeFired string        `json:"time_fired"`
}

type haEventData struct {
	EntityID string   `json:"entity_id"`
	OldState *haState `json:"old_state"`
	NewState *haState `json:"new_state"`
}

type haState struct {
	State string `json:"state"`
}

func (l *listener) connectAndServe(ctx context.Context) error {
	conn, _, err := l.dialer.DialContext(ctx, l.url, http.Header{})
	if err != nil {
		return fmt.Errorf("dial: %w", err)
	}
	defer conn.Close()

	// 1. auth_required
	var first serverMsg
	if err := readJSONCtx(ctx, conn, &first); err != nil {
		return fmt.Errorf("read auth_required: %w", err)
	}
	if first.Type != "auth_required" {
		return fmt.Errorf("expected auth_required, got %q", first.Type)
	}

	// 2. send auth
	if err := conn.WriteJSON(map[string]any{
		"type":         "auth",
		"access_token": l.token,
	}); err != nil {
		return fmt.Errorf("send auth: %w", err)
	}

	// 3. auth_ok / auth_invalid
	var authResp serverMsg
	if err := readJSONCtx(ctx, conn, &authResp); err != nil {
		return fmt.Errorf("read auth response: %w", err)
	}
	switch authResp.Type {
	case "auth_ok":
		l.log.InfoContext(ctx, "ha auth_ok")
		l.notifyConnected(true)
	case "auth_invalid":
		return fmt.Errorf("%w: %s", errAuthInvalid, authResp.Message)
	default:
		return fmt.Errorf("unexpected auth response %q", authResp.Type)
	}

	// 4. subscribe_events
	subID := l.msgID.Add(1)
	if err := conn.WriteJSON(map[string]any{
		"id":         subID,
		"type":       "subscribe_events",
		"event_type": "state_changed",
	}); err != nil {
		return fmt.Errorf("send subscribe: %w", err)
	}

	// 5. read loop
	for {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		var msg serverMsg
		if err := readJSONCtx(ctx, conn, &msg); err != nil {
			return fmt.Errorf("read: %w", err)
		}
		if msg.Type != "event" || msg.Event == nil {
			continue
		}
		if msg.Event.EventType != "state_changed" {
			continue
		}
		data := msg.Event.Data
		if data.EntityID != l.entityID {
			continue
		}
		old, new := "", ""
		if data.OldState != nil {
			old = data.OldState.State
		}
		if data.NewState != nil {
			new = data.NewState.State
		}
		select {
		case l.events <- Event{EntityID: data.EntityID, OldState: old, NewState: new}:
		case <-ctx.Done():
			return ctx.Err()
		}
	}
}

func (l *listener) notifyConnected(up bool) {
	select {
	case l.connected <- up:
	default:
		// buffer full; drop — the next transition will overwrite semantics.
	}
}

// readJSONCtx wraps conn.ReadJSON with a ctx-cancellable goroutine. gorilla
// doesn't natively honour ctx on reads, so we set a long deadline and close
// the conn if ctx is cancelled.
func readJSONCtx(ctx context.Context, conn *websocket.Conn, v any) error {
	done := make(chan error, 1)
	go func() { done <- conn.ReadJSON(v) }()
	select {
	case err := <-done:
		return err
	case <-ctx.Done():
		_ = conn.Close()
		<-done
		return ctx.Err()
	}
}
