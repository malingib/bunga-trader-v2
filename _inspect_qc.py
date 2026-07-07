import inspect, quantcontext.server as s
for fn in ['screen_stocks','backtest_strategy','factor_analysis']:
    f = getattr(s, fn, None)
    print('===' + fn + '===')
    try:
        print(inspect.signature(f))
    except Exception as e:
        print('sig err', e)
    doc = (f.__doc__ or '')[:1200]
    print(doc)
    print('--- source (first 120 lines) ---')
    try:
        src = inspect.getsource(f)
        print('\n'.join(src.splitlines()[:120]))
    except Exception as e:
        print('src err', e)
    print('\n\n')
