"""PEBP Studio: a local, step-by-step clinician front end for the `pebp` pipeline.

`python -m pebp studio` starts a small web server on 127.0.0.1 and opens it in the browser. Every
geometric step runs the same `python -m pebp ...` command a person would type, so the studio adds no
second implementation of the design: it only decides what is shown, in what order, and records who
approved each step (`review.json` beside `design.json`).
"""
