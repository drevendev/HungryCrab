module example.com/polyglot
go 1.23
require (
    github.com/stretchr/testify v1.9.0
)
exclude (
    example.com/excluded v1.0.0
)
replace (
    example.com/replaced v1.0.0 => example.com/replacement v1.1.0
)
