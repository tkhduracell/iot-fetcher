package update

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"reflect"
	"testing"
	"time"

	"github.com/moby/moby/api/types/container"
	ocispec "github.com/opencontainers/image-spec/specs-go/v1"
	"github.com/tkhduracell/iot-fetcher/watchdog/internal/dockerx"
	"github.com/tkhduracell/iot-fetcher/watchdog/internal/metrics"
)

type fakeDocker struct {
	containers []dockerx.Container
	remote     string
	local      []string
	startErr   error // returned when starting the new container
	onStop     func()
	calls      []string
}

func (f *fakeDocker) rec(s string)                                           { f.calls = append(f.calls, s) }
func (f *fakeDocker) List(context.Context) ([]dockerx.Container, error)      { return f.containers, nil }
func (f *fakeDocker) Restart(context.Context, string) error                  { return nil }
func (f *fakeDocker) RemoteDigest(context.Context, string) (string, error)   { return f.remote, nil }
func (f *fakeDocker) LocalDigests(context.Context, string) ([]string, error) { return f.local, nil }
func (f *fakeDocker) ImageDefaults(context.Context, string) (*ocispec.ImageConfig, error) {
	return &ocispec.ImageConfig{}, nil
}
func (f *fakeDocker) Pull(_ context.Context, ref string) error { f.rec("pull " + ref); return nil }
func (f *fakeDocker) Prune(context.Context) error              { f.rec("prune"); return nil }
func (f *fakeDocker) Stop(ctx context.Context, id string) error {
	f.rec("stop " + id)
	if f.onStop != nil {
		f.onStop()
	}
	return ctx.Err()
}
func (f *fakeDocker) Rename(_ context.Context, id, n string) error {
	f.rec("rename " + id + " " + n)
	return nil
}
func (f *fakeDocker) Remove(_ context.Context, id string) error { f.rec("remove " + id); return nil }
func (f *fakeDocker) Inspect(_ context.Context, id string) (dockerx.Spec, error) {
	f.rec("inspect " + id)
	return dockerx.Spec{Name: "app", Config: &container.Config{}, HostConfig: &container.HostConfig{}}, nil
}
func (f *fakeDocker) Create(_ context.Context, s dockerx.Spec) (string, error) {
	f.rec("create " + s.Name + " " + s.Config.Image)
	return "new", nil
}
func (f *fakeDocker) Start(_ context.Context, id string) error {
	f.rec("start " + id)
	if id == "new" {
		return f.startErr
	}
	return nil
}

type nopMetrics struct{ points []*metrics.Point }

func (m *nopMetrics) Write(_ context.Context, p []*metrics.Point) error {
	m.points = append(m.points, p...)
	return nil
}

const img = "europe-docker.pkg.dev/p/images/app:latest"

func newUpdater(fd *fakeDocker) (*Updater, *nopMetrics) {
	fm := &nopMetrics{}
	return &Updater{
		Docker: fd, Metrics: fm,
		Log:    slog.New(slog.NewTextHandler(io.Discard, nil)),
		Labels: []string{"watchdog.update", "wud.watch"}, SelfName: "watchdog",
		Prune: true, Now: time.Now,
	}, fm
}

func app(name string) dockerx.Container {
	return dockerx.Container{ID: "old", Name: name, Image: img, ImageID: "sha256:abc",
		Labels: map[string]string{"wud.watch": "true"}}
}

func TestTick_SameDigestIsNoop(t *testing.T) {
	fd := &fakeDocker{containers: []dockerx.Container{app("app")},
		remote: "sha256:111", local: []string{"europe-docker.pkg.dev/p/images/app@sha256:111"}}
	u, _ := newUpdater(fd)
	avail, err := u.Tick(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(fd.calls) != 0 || len(avail) != 0 {
		t.Fatalf("calls=%v avail=%v, want no-op", fd.calls, avail)
	}
}

func TestTick_NewDigestRecreatesInOrder(t *testing.T) {
	fd := &fakeDocker{containers: []dockerx.Container{app("app")},
		remote: "sha256:222", local: []string{"europe-docker.pkg.dev/p/images/app@sha256:111"}}
	u, fm := newUpdater(fd)
	avail, err := u.Tick(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	want := []string{
		"pull " + img, "inspect old", "stop old", "rename old app-watchdog-old",
		"create app " + img, "start new", "remove old", "prune",
	}
	if !reflect.DeepEqual(fd.calls, want) {
		t.Fatalf("calls:\n got %v\nwant %v", fd.calls, want)
	}
	if avail["app"] {
		t.Fatal("applied update still reported as available")
	}
	if len(fm.points) != 1 || fm.points[0].Fields["ok"] != 1 {
		t.Fatalf("metrics = %+v", fm.points)
	}
}

func TestTick_StartFailureRollsBack(t *testing.T) {
	fd := &fakeDocker{containers: []dockerx.Container{app("app")},
		remote: "sha256:222", local: []string{"x@sha256:111"}, startErr: errors.New("boom")}
	u, fm := newUpdater(fd)
	if _, err := u.Tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	want := []string{
		"pull " + img, "inspect old", "stop old", "rename old app-watchdog-old",
		"create app " + img, "start new",
		"remove new", "rename old app", "start old",
	}
	if !reflect.DeepEqual(fd.calls, want) {
		t.Fatalf("calls:\n got %v\nwant %v", fd.calls, want)
	}
	if fm.points[0].Fields["rolled_back"] != 1 || fm.points[0].Fields["ok"] != 0 {
		t.Fatalf("metrics = %+v", fm.points[0].Fields)
	}
}

func TestTick_SkipsSelfUnlabelledAndDryRun(t *testing.T) {
	unlabelled := app("other")
	unlabelled.Labels = nil
	fd := &fakeDocker{containers: []dockerx.Container{app("watchdog"), unlabelled},
		remote: "sha256:222", local: []string{"x@sha256:111"}}
	u, _ := newUpdater(fd)
	avail, _ := u.Tick(context.Background())
	if len(fd.calls) != 0 {
		t.Fatalf("calls = %v, want none", fd.calls)
	}
	if !avail["watchdog"] || avail["other"] {
		t.Fatalf("avail = %v", avail)
	}

	fd.containers = []dockerx.Container{app("app")}
	u.DryRun = true
	u.Tick(context.Background())
	if len(fd.calls) != 0 {
		t.Fatalf("dry run calls = %v", fd.calls)
	}
}

// A failed digest check keeps the previous answer instead of reporting the
// container as current.
type flakyDocker struct {
	*fakeDocker
	fail bool
}

func (f *flakyDocker) RemoteDigest(ctx context.Context, ref string) (string, error) {
	if f.fail {
		return "", errors.New("registry 503")
	}
	return f.fakeDocker.RemoteDigest(ctx, ref)
}

func TestTick_DigestErrorKeepsLastState(t *testing.T) {
	fd := &flakyDocker{fakeDocker: &fakeDocker{containers: []dockerx.Container{app("watchdog")},
		remote: "sha256:222", local: []string{"x@sha256:111"}}}
	u, _ := newUpdater(fd.fakeDocker)
	u.Docker = fd
	if avail, _ := u.Tick(context.Background()); !avail["watchdog"] {
		t.Fatal("expected update available")
	}
	fd.fail = true
	if avail, _ := u.Tick(context.Background()); !avail["watchdog"] {
		t.Fatal("digest error must keep the previous 'available' state")
	}
}

// The swap finishes even if the caller's context is cancelled mid-update.
func TestTick_CancelledContextStillRollsBack(t *testing.T) {
	fd := &fakeDocker{containers: []dockerx.Container{app("app")},
		remote: "sha256:222", local: []string{"x@sha256:111"}, startErr: errors.New("boom")}
	u, _ := newUpdater(fd)
	ctx, cancel := context.WithCancel(context.Background())
	fd.onStop = cancel
	u.Tick(ctx)
	if got := fd.calls[len(fd.calls)-1]; got != "start old" {
		t.Fatalf("last call = %q, want rollback to start the old container", got)
	}
}
