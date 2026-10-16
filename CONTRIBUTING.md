# Contributing

1. Keep the core deterministic and dependency-free.
2. Add tests for input validation, evidence mapping, and report status changes.
3. Do not silently interpret unsupported SystemVerilog syntax as verified
   evidence; emit a warning or leave evidence unresolved.
4. Keep parser, simulator, and coverage adapters behind small interfaces so
   each can be tested independently.
5. Run `python -m unittest discover -s tests -v` before submitting changes.

Please do not copy text or examples from proprietary specifications into the
repository. Use requirement IDs and original, project-specific descriptions.
