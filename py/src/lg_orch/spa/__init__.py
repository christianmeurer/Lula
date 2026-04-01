# SPDX-License-Identifier: MIT
"""Lula Console SPA — Leptos (Rust/WASM) build output served as static files.

The SPA is built by ``trunk build --release`` in ``rs/spa-leptos/`` and output
to ``rs/spa-leptos/dist/``. The Python API serves these files at ``/app/``.

Set ``LG_SPA_DIST_DIR`` to override the dist path (useful in Docker).
"""
