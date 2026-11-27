import os

# numpy's OpenBLAS reserves memory for one thread per core when it loads (about 230 MB of commit on a 4-core laptop).
# Sniff's only matrix work is a 13 x 384 product per document, so one thread is plenty. Set before numpy is imported.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
