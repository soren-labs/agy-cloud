"""Service boundary protocols (T0 interfaces; unimplemented on purpose).

Each Protocol documents the transaction semantics that T2/T5/T6/T8/T9/T10 must
implement. The protocols are structural (typing.Protocol): implementations are
checked statically and by tests/test_protocols.py for shape agreement.

Docstrings here are normative contract text, mirrored in docs/CONTRACTS.md.
"""
