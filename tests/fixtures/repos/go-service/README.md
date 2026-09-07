# moltd

A small daemon that watches a burrow and reports when its occupant is about to moult.

## Install

```
go install github.com/crustacean/moltd/cmd/moltd@latest
```

## Usage

```
moltd --burrow /var/lib/burrow --interval 30s
```

## Development

`make test` runs the suite; `make lint` runs golangci-lint.
