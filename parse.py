#!/usr/bin/env python3

NUM_SECONDS = 5

if __name__ == '__main__':
    with open('sv6-mmaps.dat', 'w') as f1:
        with open('serial.log') as f2:
            flag = False
            for line in f2.readlines():
                if 'vmops' in line:
                    flag = True
                    # threads.append(line.split(' ')[2])
                    f1.write(line.split(' ')[2] + ' ')
                if flag is True and 'mmaps' in line:
                    flag = False
                    thp = float(line.split(' ')[0]) / (1000000 * NUM_SECONDS)
                    f1.write(str(thp) + '\n')
