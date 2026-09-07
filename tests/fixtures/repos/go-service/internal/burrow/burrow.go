// Package burrow reads the on-disk state of a single burrow.
package burrow

import (
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// ErrEmpty is returned when a burrow directory exists but holds no occupant.
var ErrEmpty = errors.New("burrow is empty")

// Burrow is one occupied burrow on disk.
type Burrow struct {
	Path   string
	Instar int
}

// Open reads the burrow at path.
func Open(path string) (*Burrow, error) {
	raw, err := os.ReadFile(filepath.Join(path, "instar"))
	if err != nil {
		if os.IsNotExist(err) {
			return nil, ErrEmpty
		}
		return nil, err
	}
	instar, err := strconv.Atoi(strings.TrimSpace(string(raw)))
	if err != nil {
		return nil, err
	}
	return &Burrow{Path: path, Instar: instar}, nil
}

// Ready reports whether the occupant is due to moult.
func (b *Burrow) Ready() bool {
	return b.Instar >= 5
}
