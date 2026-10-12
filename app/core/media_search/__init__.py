#!/usr/bin/env python3
"""
Media search: finding pictures, footage and sound by describing them.

EmbeddingGemma 2 puts text, pictures and sound in one vector space, so a
typed description lands beside the frames and sounds it describes. It runs on
this computer in Buddy's own llama.cpp (pages/manual_chat/local_llama.py) -
nothing is uploaded.

  model.py     what has to be downloaded, and the calls that turn a
               description, a picture or a sound into a vector.
  extract.py   a file into the pieces that are embedded: a still as itself,
               footage as a frame every few seconds (ones that look like the
               last are skipped), sound as half-minute windows.
  store.py     the index, one SQLite file in ~/.buddy/media_search: every
               piece's vector under its file, found again by path, size and
               time changed - a file is embedded once, whichever tool asked.
  indexer.py   the one background thread that works through a queue of
               files, so two tools never run the model at once.

Asset Manager searches its library with it; Footage Search searches the
Media Pool's clips and suggests footage for a transcript's lines.

No Qt in this package: pages hear from the indexer's thread through
callbacks and pass them to their own thread themselves.
"""
