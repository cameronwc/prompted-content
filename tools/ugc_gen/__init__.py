"""UGC reaction video composer pipeline for tools/ugc.py.

Mirrors tools/reels_gen/'s shape: this package holds all the logic, the
top-level tools/ugc.py is a thin CLI. Rights gating, font loading and the
CSV shape (first_comment, category hashtags) are reused from
tools/pinterest and tools/reels_gen rather than duplicated.
"""
