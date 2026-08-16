"""chesspdf — chess-book PDF → verified puzzle PGN pipeline.

Architecture: a Bundle directory is the single source of truth; each stage is
an idempotent function (bundle in, bundle state out) so the CLI, a web app,
and workers can all drive the same code.

Stages: split → recognize → verify → repair → assemble.
"""

__version__ = "0.1.0"
