#!/usr/bin/env python3

import csv

NUM_SECONDS = 5

if __name__ == '__main__':
    with open('sv6_results.csv', 'w', newline='') as csvfile:
        writer = csv.writer(csvfile, delimiter=',',
                            quotechar='|', quoting=csv.QUOTE_MINIMAL)
        writer.writerow(['threads', 'throuhput'])
        with open('serial.log') as logfile:
            flag = False
            for line in logfile.readlines():
                if 'vmops' in line:
                    flag = True
                    threads = line.split(' ')[2]
                if flag is True and 'mmaps' in line:
                    flag = False
                    thp = float(line.split(' ')[0]) / (1000000 * NUM_SECONDS)
                    writer.writerow([threads, thp])
