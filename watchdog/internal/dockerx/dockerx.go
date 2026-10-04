// Package dockerx is the thin slice of the Docker API the watchdog needs,
// behind an interface so the health and update loops can be tested with fakes.
package dockerx

import (
	"context"
	"fmt"
	"slices"
	"strings"
	"time"

	"github.com/moby/moby/api/types/container"
	"github.com/moby/moby/api/types/network"
	"github.com/moby/moby/client"
	ocispec "github.com/opencontainers/image-spec/specs-go/v1"
)

// Per-call deadlines, so one stuck Docker or registry call can't wedge a loop.
var (
	callTimeout = 2 * time.Minute
	pullTimeout = 20 * time.Minute
)

// Container is the per-container view both loops work from.
type Container struct {
	ID      string
	Name    string
	Image   string // the reference it was created from, e.g. repo/img:latest
	ImageID string
	State   string // running, exited, ...
	// Health is "" when the container has no healthcheck, otherwise
	// starting / healthy / unhealthy.
	Health string
	Labels map[string]string
}

// Spec is everything needed to recreate a container identically.
type Spec struct {
	Name       string
	Config     *container.Config
	HostConfig *container.HostConfig
	Networks   map[string]*network.EndpointSettings
}

type Docker interface {
	List(ctx context.Context) ([]Container, error)
	Restart(ctx context.Context, id string) error

	// RemoteDigest is the registry's current manifest digest for ref.
	RemoteDigest(ctx context.Context, ref string) (string, error)
	// LocalDigests are the repo digests of a local image ID.
	LocalDigests(ctx context.Context, imageID string) ([]string, error)
	// ImageDefaults is the config baked into a local image (CMD, ENV, ...).
	ImageDefaults(ctx context.Context, imageID string) (*ocispec.ImageConfig, error)
	Pull(ctx context.Context, ref string) error
	Prune(ctx context.Context) error

	Inspect(ctx context.Context, id string) (Spec, error)
	Stop(ctx context.Context, id string) error
	Start(ctx context.Context, id string) error
	Rename(ctx context.Context, id, name string) error
	Remove(ctx context.Context, id string) error
	Create(ctx context.Context, s Spec) (string, error)
}

type Client struct{ c *client.Client }

func New() (*Client, error) {
	c, err := client.New(client.FromEnv, client.WithAPIVersionNegotiation())
	if err != nil {
		return nil, err
	}
	return &Client{c: c}, nil
}

func (d *Client) List(ctx context.Context) ([]Container, error) {
	ctx, cancel := context.WithTimeout(ctx, callTimeout)
	defer cancel()
	res, err := d.c.ContainerList(ctx, client.ContainerListOptions{})
	if err != nil {
		return nil, err
	}
	out := make([]Container, 0, len(res.Items))
	for _, s := range res.Items {
		c := Container{
			ID:      s.ID,
			Image:   s.Image,
			ImageID: s.ImageID,
			State:   string(s.State),
			Labels:  s.Labels,
		}
		if len(s.Names) > 0 {
			c.Name = strings.TrimPrefix(s.Names[0], "/")
		}
		c.Health = healthOf(s)
		if strings.HasPrefix(c.Image, "sha256:") {
			// The tag moved to a newer image outside the watchdog (e.g. a
			// manual pull), so Docker lists the bare ID. The container's own
			// config still has the reference it was created from.
			if ci, err := d.c.ContainerInspect(ctx, s.ID, client.ContainerInspectOptions{}); err == nil && ci.Container.Config != nil {
				c.Image = ci.Container.Config.Image
			}
		}
		out = append(out, c)
	}
	return out, nil
}

// healthOf prefers the structured Health field (API >= 1.52) and falls back
// to the "(unhealthy)" suffix of Status that older engines, e.g. Docker 28 on
// rpi5, return instead.
func healthOf(s container.Summary) string {
	if s.Health != nil {
		if s.Health.Status == container.NoHealthcheck {
			return ""
		}
		return string(s.Health.Status)
	}
	for _, h := range []container.HealthStatus{container.Unhealthy, container.Healthy, container.Starting} {
		if strings.Contains(s.Status, "("+string(h)+")") || strings.Contains(s.Status, "health: "+string(h)) {
			return string(h)
		}
	}
	return ""
}

func (d *Client) Restart(ctx context.Context, id string) error {
	ctx, cancel := context.WithTimeout(ctx, callTimeout)
	defer cancel()
	timeout := 30
	_, err := d.c.ContainerRestart(ctx, id, client.ContainerRestartOptions{Timeout: &timeout})
	return err
}

func (d *Client) RemoteDigest(ctx context.Context, ref string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, callTimeout)
	defer cancel()
	res, err := d.c.DistributionInspect(ctx, ref, client.DistributionInspectOptions{})
	if err != nil {
		return "", err
	}
	return res.Descriptor.Digest.String(), nil
}

func (d *Client) LocalDigests(ctx context.Context, imageID string) ([]string, error) {
	ctx, cancel := context.WithTimeout(ctx, callTimeout)
	defer cancel()
	res, err := d.c.ImageInspect(ctx, imageID)
	if err != nil {
		return nil, err
	}
	return res.RepoDigests, nil
}

func (d *Client) ImageDefaults(ctx context.Context, imageID string) (*ocispec.ImageConfig, error) {
	ctx, cancel := context.WithTimeout(ctx, callTimeout)
	defer cancel()
	res, err := d.c.ImageInspect(ctx, imageID)
	if err != nil {
		return nil, err
	}
	if res.Config == nil {
		return &ocispec.ImageConfig{}, nil
	}
	return &res.Config.ImageConfig, nil
}

func (d *Client) Pull(ctx context.Context, ref string) error {
	ctx, cancel := context.WithTimeout(ctx, pullTimeout)
	defer cancel()
	resp, err := d.c.ImagePull(ctx, ref, client.ImagePullOptions{})
	if err != nil {
		return err
	}
	defer resp.Close()
	return resp.Wait(ctx)
}

func (d *Client) Prune(ctx context.Context) error {
	ctx, cancel := context.WithTimeout(ctx, callTimeout)
	defer cancel()
	f := client.Filters{}.Add("dangling", "true")
	_, err := d.c.ImagePrune(ctx, client.ImagePruneOptions{Filters: f})
	return err
}

func (d *Client) Inspect(ctx context.Context, id string) (Spec, error) {
	ctx, cancel := context.WithTimeout(ctx, callTimeout)
	defer cancel()
	res, err := d.c.ContainerInspect(ctx, id, client.ContainerInspectOptions{})
	if err != nil {
		return Spec{}, err
	}
	ci := res.Container
	s := Spec{
		Name:       strings.TrimPrefix(ci.Name, "/"),
		Config:     ci.Config,
		HostConfig: ci.HostConfig,
	}
	if ci.NetworkSettings != nil {
		s.Networks = ci.NetworkSettings.Networks
	}
	return s, nil
}

func (d *Client) Stop(ctx context.Context, id string) error {
	ctx, cancel := context.WithTimeout(ctx, callTimeout)
	defer cancel()
	timeout := 30
	_, err := d.c.ContainerStop(ctx, id, client.ContainerStopOptions{Timeout: &timeout})
	return err
}

func (d *Client) Start(ctx context.Context, id string) error {
	ctx, cancel := context.WithTimeout(ctx, callTimeout)
	defer cancel()
	_, err := d.c.ContainerStart(ctx, id, client.ContainerStartOptions{})
	return err
}

func (d *Client) Rename(ctx context.Context, id, name string) error {
	ctx, cancel := context.WithTimeout(ctx, callTimeout)
	defer cancel()
	_, err := d.c.ContainerRename(ctx, id, client.ContainerRenameOptions{NewName: name})
	return err
}

func (d *Client) Remove(ctx context.Context, id string) error {
	ctx, cancel := context.WithTimeout(ctx, callTimeout)
	defer cancel()
	_, err := d.c.ContainerRemove(ctx, id, client.ContainerRemoveOptions{Force: true})
	return err
}

// Create makes a container from s. The create call accepts only one network
// endpoint, so any extra networks are connected afterwards, before start.
func (d *Client) Create(ctx context.Context, s Spec) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, callTimeout)
	defer cancel()
	var first string
	for name := range s.Networks {
		if first == "" || name == string(s.HostConfig.NetworkMode) {
			first = name
		}
	}
	nc := &network.NetworkingConfig{EndpointsConfig: map[string]*network.EndpointSettings{}}
	if first != "" {
		nc.EndpointsConfig[first] = endpoint(s.Networks[first])
	}
	res, err := d.c.ContainerCreate(ctx, client.ContainerCreateOptions{
		Name:             s.Name,
		Config:           s.Config,
		HostConfig:       s.HostConfig,
		NetworkingConfig: nc,
	})
	if err != nil {
		return "", err
	}
	for name, ep := range s.Networks {
		if name == first {
			continue
		}
		if _, err := d.c.NetworkConnect(ctx, name, client.NetworkConnectOptions{
			Container:      res.ID,
			EndpointConfig: endpoint(ep),
		}); err != nil {
			return res.ID, fmt.Errorf("connect %s: %w", name, err)
		}
	}
	return res.ID, nil
}

// endpoint keeps the user-set parts of an endpoint (aliases, static IPs) and
// drops the runtime ones (IDs, assigned addresses) the daemon fills in.
func endpoint(ep *network.EndpointSettings) *network.EndpointSettings {
	if ep == nil {
		return nil
	}
	return &network.EndpointSettings{
		IPAMConfig: ep.IPAMConfig,
		Links:      ep.Links,
		Aliases:    ep.Aliases,
		DriverOpts: ep.DriverOpts,
	}
}

// StripImageDefaults clears the parts of an inspected container config that
// merely repeat the old image's own defaults, so the recreated container picks
// up the new image's CMD/ENTRYPOINT/ENV/... instead of pinning the old ones.
// Values the user set explicitly (compose command:, environment:, labels:)
// differ from the image defaults and are kept. Same approach as Watchtower.
func StripImageDefaults(c *container.Config, img *ocispec.ImageConfig) {
	if img == nil {
		return
	}
	if slices.Equal(c.Cmd, img.Cmd) {
		c.Cmd = nil
	}
	if slices.Equal(c.Entrypoint, img.Entrypoint) {
		c.Entrypoint = nil
	}
	if c.WorkingDir == img.WorkingDir {
		c.WorkingDir = ""
	}
	if c.User == img.User {
		c.User = ""
	}
	if c.StopSignal == img.StopSignal {
		c.StopSignal = ""
	}
	c.Env = slices.DeleteFunc(c.Env, func(e string) bool { return slices.Contains(img.Env, e) })
	for k, v := range img.Labels {
		if c.Labels[k] == v {
			delete(c.Labels, k)
		}
	}
	for p := range c.ExposedPorts {
		if _, ok := img.ExposedPorts[fmt.Sprint(p)]; ok {
			delete(c.ExposedPorts, p)
		}
	}
	for v := range c.Volumes {
		if _, ok := img.Volumes[v]; ok {
			delete(c.Volumes, v)
		}
	}
}
