# Security Boundary

External Agent adapters are untrusted. Their output enters DevRelay-X as evidence
and policy input, never as an implicit authority. Repository mutation, credential
access, and policy decisions require explicit boundaries and are not granted to
the base adapter.
