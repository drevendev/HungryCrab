# ADR 0001: Use click for the command line

## Status

Accepted

## Context

argparse is enough for one command, but the tool will grow subcommands.

## Decision

Use click.

## Consequences

One runtime dependency; nicer help output.
