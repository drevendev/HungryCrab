// Command moltd watches a burrow and reports when its occupant is about to moult.
package main

import (
	"fmt"
	"os"
	"time"

	"github.com/spf13/cobra"

	"github.com/crustacean/moltd/internal/burrow"
	"github.com/crustacean/moltd/internal/shell"
)

func main() {
	var interval time.Duration
	var path string

	root := &cobra.Command{
		Use:   "moltd",
		Short: "Watch a burrow and report imminent moults",
		RunE: func(cmd *cobra.Command, args []string) error {
			b, err := burrow.Open(path)
			if err != nil {
				return fmt.Errorf("open burrow: %w", err)
			}
			return shell.Watch(cmd.Context(), b, interval)
		},
	}
	root.Flags().StringVar(&path, "burrow", "/var/lib/burrow", "burrow directory")
	root.Flags().DurationVar(&interval, "interval", 30*time.Second, "poll interval")

	if err := root.Execute(); err != nil {
		fmt.Fprintln(os.Stderr, "moltd:", err)
		os.Exit(1)
	}
}
