package ha

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

func newTestServer(t *testing.T, handler func(*websocket.Conn, *testing.T)) (string, func()) {
	t.Helper()
	upgrader := websocket.Upgrader{
		CheckOrigin: func(r *http.Request) bool { return true },
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			t.Logf("upgrade: %v", err)
			return
		}
		defer conn.Close()
		handler(conn, t)
	}))
	url := "ws" + strings.TrimPrefix(srv.URL, "http")
	return url, srv.Close
}

func quietLog() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

func TestDial_AuthOK_ForwardsMatchingEvent(t *testing.T) {
	url, stop := newTestServer(t, func(c *websocket.Conn, t *testing.T) {
		_ = c.WriteJSON(map[string]any{"type": "auth_required"})
		var auth map[string]any
		if err := c.ReadJSON(&auth); err != nil {
			return
		}
		if auth["access_token"] != "tok" {
			_ = c.WriteJSON(map[string]any{"type": "auth_invalid", "message": "bad token"})
			return
		}
		_ = c.WriteJSON(map[string]any{"type": "auth_ok"})

		// Expect subscribe
		var sub map[string]any
		if err := c.ReadJSON(&sub); err != nil {
			return
		}

		// Non-matching entity — should be filtered out
		_ = c.WriteJSON(serverMsg{
			Type: "event",
			Event: &haEvent{
				EventType: "state_changed",
				Data: haEventData{
					EntityID: "sensor.other",
					OldState: &haState{State: "x"},
					NewState: &haState{State: "y"},
				},
			},
		})

		// Matching entity — should be forwarded
		_ = c.WriteJSON(serverMsg{
			Type: "event",
			Event: &haEvent{
				EventType: "state_changed",
				Data: haEventData{
					EntityID: "sensor.wallbox_status",
					OldState: &haState{State: "Ready"},
					NewState: &haState{State: "Charging"},
				},
			},
		})

		// Keep connection open until client hangs up.
		_, _, _ = c.ReadMessage()
	})
	defer stop()

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	l := Dial(ctx, url, "tok", "sensor.wallbox_status", quietLog())

	// Should see connected=true
	select {
	case up := <-l.Connected():
		if !up {
			t.Fatal("expected connected=true")
		}
	case <-time.After(time.Second):
		t.Fatal("timeout waiting for auth_ok")
	}

	// Should see exactly one filtered event.
	select {
	case ev := <-l.Events():
		if ev.EntityID != "sensor.wallbox_status" || ev.OldState != "Ready" || ev.NewState != "Charging" {
			t.Errorf("unexpected event: %+v", ev)
		}
	case <-time.After(time.Second):
		t.Fatal("timeout waiting for filtered event")
	}
}

func TestDial_AuthInvalid_Stops(t *testing.T) {
	connectCount := 0
	url, stop := newTestServer(t, func(c *websocket.Conn, t *testing.T) {
		connectCount++
		_ = c.WriteJSON(map[string]any{"type": "auth_required"})
		var auth map[string]any
		if err := c.ReadJSON(&auth); err != nil {
			return
		}
		_ = c.WriteJSON(map[string]any{"type": "auth_invalid", "message": "bad token"})
	})
	defer stop()

	ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
	defer cancel()

	l := Dial(ctx, url, "bad", "sensor.wallbox_status", quietLog())

	// Give the loop a moment to try and then give up.
	time.Sleep(200 * time.Millisecond)

	// Connected may or may not have emitted; we only assert that the loop
	// stopped reconnecting. Count should be exactly 1.
	if connectCount != 1 {
		t.Errorf("expected 1 connect on auth_invalid, got %d", connectCount)
	}
	_ = l
}

// Ensure JSON round-trips the server-message shape we rely on.
func TestServerMsg_JSONShape(t *testing.T) {
	raw := `{"type":"event","id":1,"event":{"event_type":"state_changed","data":{"entity_id":"s.w","old_state":{"state":"a"},"new_state":{"state":"b"}},"time_fired":"2026-04-21T00:00:00"}}`
	var msg serverMsg
	if err := json.Unmarshal([]byte(raw), &msg); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if msg.Event == nil || msg.Event.Data.EntityID != "s.w" {
		t.Fatalf("entity id not parsed: %+v", msg)
	}
	if msg.Event.Data.NewState.State != "b" {
		t.Fatalf("new state not parsed: %+v", msg.Event.Data.NewState)
	}
}
