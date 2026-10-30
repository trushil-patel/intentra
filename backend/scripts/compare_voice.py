"""Render two opt-in diagnostic JSONL runs as the requested comparison table."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
from audio.benchmark import load_samples, comparison_table, configuration_table

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('hands_free')
    parser.add_argument('push_to_talk')
    parser.add_argument('--configurations', action='store_true', help='compare any two configurations, including two F8 runs')
    args=parser.parse_args()
    if args.configurations:
        print(configuration_table(load_samples(args.hands_free), load_samples(args.push_to_talk)))
        return
    print(comparison_table(load_samples(args.hands_free,'hands_free'),load_samples(args.push_to_talk,'push_to_talk')))
if __name__=='__main__': main()
