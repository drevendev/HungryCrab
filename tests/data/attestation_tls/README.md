# Attestation TLS loopback fixture

The two synthetic server certificates share the fixture leaf key. One
certificate covers the worker's approved hostname and the other covers
`wrong-host.invalid`. Both are signed by the synthetic test CA. Its issuer
key is absent from the repository. The leaf key is public test data.

The isolated test child maps the approved hostname to an ephemeral IPv4
loopback listener and loads only the explicitly selected fixture trust root.
It keeps hostname and certificate verification enabled.
