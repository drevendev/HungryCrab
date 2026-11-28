# Attestation TLS loopback fixture

The test module uses the existing OpenSSL CLI prerequisite (also used by the
live acquisition tests) to generate a disposable CA and two signed leaf
certificates in a temporary directory outside the checkout. One certificate
covers the approved hostname and the other covers `wrong-host.invalid`.
The temporary directory is removed after the module's tests; no credential
material is tracked in this fixture directory.

The isolated test child maps the approved hostname to an ephemeral IPv4
loopback listener and loads only the explicitly selected fixture trust root.
Both ends require TLS 1.2 or newer; hostname and certificate verification
remain enabled on the client.
