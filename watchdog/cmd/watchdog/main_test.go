package main

import "testing"

func TestSplitRef(t *testing.T) {
	for _, tc := range []struct{ ref, name, tag string }{
		{"europe-docker.pkg.dev/p/images/app:latest", "europe-docker.pkg.dev/p/images/app", "latest"},
		{"localhost:5000/app", "localhost:5000/app", "latest"},
		{"ghcr.io/tkhduracell/sonos-http-api:master", "ghcr.io/tkhduracell/sonos-http-api", "master"},
		{"busybox", "busybox", "latest"},
		{"sha256:4ac46ad4ce3ee70f7f709ac2c7095d0000000000000000000000000000000000", "", ""},
	} {
		name, tag := splitRef(tc.ref)
		if name != tc.name || tag != tc.tag {
			t.Errorf("splitRef(%q) = (%q, %q), want (%q, %q)", tc.ref, name, tag, tc.name, tc.tag)
		}
	}
}
