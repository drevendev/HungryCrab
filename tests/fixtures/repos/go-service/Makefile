.PHONY: build test lint

build:
	go build ./...

test:
	go test -race -coverprofile=coverage.out ./...

lint:
	golangci-lint run
