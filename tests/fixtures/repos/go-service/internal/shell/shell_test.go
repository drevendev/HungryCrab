package shell

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/crustacean/moltd/internal/burrow"
)

func TestStageFor(t *testing.T) {
	assert.Equal(t, StageIntermoult, StageFor(1))
	assert.Equal(t, StagePremoult, StageFor(5))
	assert.Equal(t, StageEcdysis, StageFor(7))
}

func TestWatchStopsWithTheContext(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	err := Watch(ctx, &burrow.Burrow{Path: "/tmp/burrow", Instar: 5}, time.Millisecond)
	require.ErrorIs(t, err, context.Canceled)
}
