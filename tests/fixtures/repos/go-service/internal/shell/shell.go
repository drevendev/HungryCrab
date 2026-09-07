// Package shell polls a burrow and announces a moult.
package shell

import (
	"context"
	"fmt"
	"time"

	"github.com/crustacean/moltd/internal/burrow"
)

// Stage is how far along a moult is.
type Stage string

const (
	StageIntermoult Stage = "intermoult"
	StagePremoult   Stage = "premoult"
	StageEcdysis    Stage = "ecdysis"
)

// StageFor maps an instar to the stage it implies.
func StageFor(instar int) Stage {
	switch {
	case instar >= 7:
		return StageEcdysis
	case instar >= 5:
		return StagePremoult
	default:
		return StageIntermoult
	}
}

// Watch polls b until the context is cancelled, announcing every stage change.
func Watch(ctx context.Context, b *burrow.Burrow, interval time.Duration) error {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	last := StageFor(b.Instar)
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
			if stage := StageFor(b.Instar); stage != last {
				fmt.Printf("%s: %s -> %s\n", b.Path, last, stage)
				last = stage
			}
		}
	}
}
