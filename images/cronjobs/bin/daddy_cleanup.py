#!/usr/bin/env python3
import arrow
import calendar
import humanize
import sys
import glob
import argparse
import pathlib
import logging
import time
import json
import datetime as dt
import collections


log = logging.getLogger()

def genPathInfos(arg_paths, recursive=False):
    for arg_path in arg_paths:
        po = pathlib.Path(arg_path)
        log.debug(po)
        igps = glob.iglob(arg_path)
        for glob_path in igps:
            path = pathlib.Path(glob_path)
            mtime = path.stat().st_mtime
            is_dir = path.is_dir()
            is_file = path.is_file()
            log.debug(f"{path}: mtime {mtime} dir? {is_dir} file? {is_file} ")
            if is_file:
                yield (path, arrow.get(mtime))


def rm(path_list, dry_run):
    now = arrow.utcnow()
    log.info("Starting deletion:")
    for path in path_list:
        if path.is_file():
            if dry_run:
                modified = arrow.get(path.stat().st_mtime)
                print(
                    "dry run Would delete:",
                    modified.strftime("%Y-%m-%d %H:%M:%S"),
                    path,
                    humanize.naturaldelta(now - modified)
                )
            else:
                path.unlink()
                log.info(f"Deleted: {path}")


def parse_args():
    p = argparse.ArgumentParser(
        """Gets the last file created this week, last week, second to last week,
        third to last week, last month, last quarter and last year by UTC time.""")
    p.add_argument("PATH", nargs="+", help="Paths or glob(s)")
    p.add_argument("--dry-run", action="store_true",
            help="Make no changes, just output information.")
    p.add_argument("--doNt-touch", "-n", metavar="N", action="store", type=int, default=1,
            help="Do not touch the last X days from today. Defaults to 1=yesterday")
    p.add_argument("--verbose", "-v", action="store_true",
            help="Produce verbose debug information.")
    return p.parse_args()

def get_bins():

    def _return(x, y):
        return _hour_0(x), _hour_235959(y)

    def _hour_0(d):
        return d.replace(hour=0, minute=0, second=0)

    def _hour_235959(d):
        return d.replace(hour=23, minute=59, second=59)

    winning_weekday = 6 # sunday?
    start = arrow.get()
    while start.weekday() != winning_weekday:
        start = start.shift(days=-1)

    # last x weeks
    def get_weeks():
        X = start
        for i in range(3):
            X = X.shift(weeks=-1)
            yield _return(X.shift(days=-6), X)

    def get_months():
        X = start
        for i in range(6):
            X = X.shift(months=-1)
            last_of_month = calendar.monthrange(X.year, X.month)[1]
            yield _return(
                arrow.get(X.year, X.month, 1),
                arrow.get(X.year, X.month, last_of_month),
            )

    def get_quarters():
        X = start
        for i in range(12):
            X = X.shift(months=-1)
            if X.month not in (3, 6, 9, 12):
                continue
            last_of_month = calendar.monthrange(X.year, X.month)[1]
            yield _return(
                arrow.get(X.year, X.month, 1),
                arrow.get(X.year, X.month, last_of_month),
            )

    def get_years():
        for i in range(20):
            year_end = arrow.get().replace(
                year=arrow.get().year - i,
                month=12,
                day=31
            )
            yield _return(
                year_end.replace(day=1, month=1),
                year_end,
            )

    yield _return(start, start)
    yield from get_weeks()
    yield from get_months()
    yield from get_quarters()
    yield from get_years()

def get_to_delete_files(path_list, days_notouch):
    now = dt.datetime.utcnow()
    log.debug(f"Now: {now}")
    bins = {}
    for bin in set(get_bins()):
        bins.setdefault(bin, [])

    to_delete = []
    for path, mt in genPathInfos(path_list):
        if (arrow.utcnow() - mt).days < days_notouch:
            continue

        for key in bins:
            if mt >= key[0] and mt <= key[1]:
                bins[key].append(path)
        else:
            to_delete.append(path)

    # keep youngest in bins
    keep = set()
    for files in bins.values():
        arr = sorted(files, key=lambda x: x.stat().st_mtime)
        to_delete += arr[1:]
        if arr:
            keep.add(arr[0])

    print("Kept:")
    print_files(keep)
    print("==========================")

    return to_delete

def print_files(files):
    size = 0
    for path in list(set(files)):
        print(path)
        size += path.stat().st_size
    return size


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    log = logging.getLogger()
    args = parse_args()
    if args.verbose:
        log.setLevel(logging.DEBUG)
    log.debug(args)
    deletion_candidates = list(sorted(set(get_to_delete_files(
        args.PATH,
        args.doNt_touch
    ))))
    if deletion_candidates:
        size = print_files(deletion_candidates)
        print("Going to delete ", humanize.naturalsize(size))
        rm(deletion_candidates, dry_run=args.dry_run)
