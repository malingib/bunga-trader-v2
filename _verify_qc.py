import sys, traceback
print("PYTHON:", sys.executable)
try:
    from quantcontext.server import screen_stocks, backtest_strategy, factor_analysis
    print("IMPORT OK")
except Exception as e:
    traceback.print_exc()
    print("IMPORT FAIL:", repr(e))
