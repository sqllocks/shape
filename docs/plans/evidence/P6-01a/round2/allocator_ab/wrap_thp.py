import ctypes, runpy, sys
ctypes.CDLL(None, use_errno=True).prctl(41, 1, 0, 0, 0)
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
