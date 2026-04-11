#!/usr/bin/env python3
import argparse
from demo_trace import TRACE_DIM_1_1
from simple_pipeline import run_demo


def main():
    parser = argparse.ArgumentParser(description='Minimal skill_evolving demo')
    parser.add_argument('--demo', choices=['dim_1_1'], default='dim_1_1')
    args = parser.parse_args()

    if args.demo == 'dim_1_1':
        trace = TRACE_DIM_1_1
    else:
        print('Unknown demo')
        return

    print('Running minimal pipeline demo...')
    res = run_demo(trace)
    if res.new_code:
        print('\nDemo completed: new skill produced.')
    else:
        print('\nDemo completed: no new skill')

if __name__ == '__main__':
    main()
