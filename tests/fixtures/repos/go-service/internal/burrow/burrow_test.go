package burrow

import (
	"os"
	"path/filepath"
	"testing"
)

func writeBurrow(t *testing.T, instar string) string {
	t.Helper()
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "instar"), []byte(instar), 0o600); err != nil {
		t.Fatalf("write instar: %v", err)
	}
	return dir
}

func TestOpenReadsTheInstar(t *testing.T) {
	b, err := Open(writeBurrow(t, "4\n"))
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	if b.Instar != 4 {
		t.Errorf("Instar = %d, want 4", b.Instar)
	}
}

func TestOpenOnAnEmptyBurrow(t *testing.T) {
	if _, err := Open(t.TempDir()); err != ErrEmpty {
		t.Errorf("err = %v, want ErrEmpty", err)
	}
}

func TestReady(t *testing.T) {
	for _, tc := range []struct {
		instar int
		want   bool
	}{{4, false}, {5, true}, {9, true}} {
		b := &Burrow{Instar: tc.instar}
		if got := b.Ready(); got != tc.want {
			t.Errorf("instar %d: Ready() = %v, want %v", tc.instar, got, tc.want)
		}
	}
}
