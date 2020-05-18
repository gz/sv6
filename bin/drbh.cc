/**
 * Nanobenchmark: Read operation
 *   RSF. PROCESS = {read the same page of /test/test.file}
 */
#include <sys/types.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#define __STDC_FORMAT_MACROS
#include <inttypes.h>
#include "fxmark.h"
#include "util.h"
#include "libutil.h"
#include "bench.h"

#include <atomic>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/mman.h>
#include <sys/types.h>

#include "libutil.h"
#include "amd64.h"
#include "rnd.hh"
#include "xsys.h"

#if !defined(XV6_USER)
#include <pthread.h>
#include <sys/wait.h>
#else
#include "types.h"
#include "user.h"
#include "pthread.h"
#include "bits.hh"
#include "kstats.hh"
#include <xv6/perf.h>
#endif

#define DURATION 10
#define ROOT_PATH "/fxmark/"

static pthread_barrier_t bar;
static volatile bool stop __mpalign__;

void* timer_thread(void *)
{
  sleep(DURATION);
  stop = true;
  return NULL;
}

static void set_shared_test_root(struct worker *worker, char *test_root)
{
	struct fx_opt *fx_opt = fx_opt_worker(worker);
	snprintf(test_root, PATH_MAX, "%s", fx_opt->root);
}

static void set_test_file(struct worker *worker, char *test_root)
{
	struct fx_opt *fx_opt = fx_opt_worker(worker);
	snprintf(test_root, PATH_MAX, "%s/n_shblk_rd.dat", fx_opt->root);
}

static int pre_work(struct worker *worker)
{
	char path[PATH_MAX];
	char page[PAGE_SIZE];
	int fd, rc = 0;

	/* a leader takes over all pre_work() */
	if (worker->id != 0)
		return 0;

	printf("%s here\n", __func__);
	/* create a test file */
	set_shared_test_root(worker, path);
	printf("path: %s\n", path);
	mkdir(path, 0777);
	/* if (rc < 0) { */
	/* 	printf("error here 1"); */
	/* 	return rc; */
	/* } */

	set_test_file(worker, path);
	if ((fd = open(path, O_CREAT | O_RDWR, S_IRWXU)) == -1) {
		printf("error here 2");
		goto err_out;
	}

	if (write(fd, page, sizeof(page)) == -1) {
		printf("error here 3");
		goto err_out;
	}

	fsync(fd);
	close(fd);
     out:
	return rc;
     err_out:
	rc = 1;
	goto out;
}

static int main_work(struct worker *worker)
{
	struct bench *bench = worker->bench;
	char path[PATH_MAX];
	char page[PAGE_SIZE];
	int fd, rc = 0;
	uint64_t iter = 0;

	set_test_file(worker, path);
	if ((fd = open(path, O_CREAT | O_RDWR, S_IRWXU)) == -1)
		goto err_out;

	for (iter = 0; !stop; ++iter) {
		if (pread(fd, page, sizeof(page), 0) == -1)
			goto err_out;
	}
	close(fd);
     out:
	worker->works = (double)iter;
	return rc;
     err_out:
	bench->stop = 1;
	stop = true;
	rc = 1;
	goto out;
}

static struct bench_operations n_shblk_rd_ops;
/* = { */
/* 	.pre_work  = pre_work, */
/* 	.main_work = main_work, */
/* }; */

static void init_bench(struct bench *bench)
{
	struct fx_opt *fx_opt = fx_opt_bench(bench);

	mkdir(ROOT_PATH, 0777);

	n_shblk_rd_ops.pre_work = pre_work;
	n_shblk_rd_ops.main_work = main_work;

	bench->duration = DURATION;
	strncpy(fx_opt->root, ROOT_PATH, PATH_MAX);
	bench->ops = n_shblk_rd_ops;
	bench->use_threads = 1;
}

static void *worker_main(void *arg)
{
        struct worker *worker = (struct worker*)arg;
        struct bench *bench = worker->bench;
        uint64_t s_clk = 1, s_us = 1;
        uint64_t e_clk = 0, e_us = 0;
        int err = 0;

        /* set affinity */
        setaffinity(worker->id);

        /* pre-work */
        if (bench->ops.pre_work) {
                err = bench->ops.pre_work(worker);
                if (err) {
			printf("error in pre work\n");
			goto err_out;
		}
        }

        /* wait for start signal */
        worker->ready = 1;
        if (worker->id) {
                while (!bench->start)
                        nop_pause();
        }
        else {
                /* are all workers ready? */
                int i;
                for (i = 1; i < bench->ncpu; i++) {
                        struct worker *w = &bench->workers[i];
                        while (!w->ready)
                                nop_pause();
                }
		/* XXX: Put something here */
		pthread_t timer;
		pthread_create(&timer, NULL, timer_thread, NULL);
                bench->start = 1;
        }

        /* start time */
        s_clk = rdtsc();
        s_us = rdtsc();

        /* main work */
        if (bench->ops.main_work) {
                err = bench->ops.main_work(worker);
                if (err)
                        goto err_out;
        }

        /* end time */
        e_clk = rdtsc();
        e_us = rdtsc();

        /* post-work */
        if (bench->ops.post_work)
                err = bench->ops.post_work(worker);
err_out:
        worker->ret = err;
        worker->usecs = e_us - s_us;
        worker->clocks = e_clk - s_clk;
	stop = 1;

        return NULL;
}
void run_bench(struct bench *bench)
{
	int i;
	for (i = 1; i < bench->ncpu; ++i) {
		/**
		 * fork() is intentionally used instead of pthread
		 * to avoid known scalability bottlenecks 
		 * of linux virtual memory subsystem. 
		 */
		if (bench->use_threads) {
			pthread_t th;
			bench->workers[i].ret =
				pthread_create(&th, NULL, worker_main,
					       (void *)&bench->workers[i]);
		} else {
			pid_t p = fork();
			if (p < 0)
				bench->workers[i].ret = 1;
			else if (!p) {
				worker_main(&bench->workers[i]);
				exit(0);
			}
		}
	}
	worker_main(&bench->workers[0]);
}

void report_bench(struct bench *bench, FILE *out)
{
        uint64_t total_rdtscs = 0;
        double   total_works = 0.0;
        double   avg_secs;
        int i, n_fg_cpu;

        /* if report_bench is overloaded */
        if (bench->ops.report_bench) {
                bench->ops.report_bench(bench, out);
                return;
        }

        /* default report_bench impl. */
        for (i = 0; i < bench->ncpu; ++i) {
                struct worker *w = &bench->workers[i];
		if (w->is_bg) continue;
		if (w->works)
			total_rdtscs += w->usecs;
                total_works += w->works;
        }
	n_fg_cpu = bench->ncpu - bench->nbg;
        avg_secs = (double)total_rdtscs/(double)n_fg_cpu/1000000.0;

	fprintf(out, "# ncpu secs works works/sec\n");
        fprintf(out, "%d %f %f %f\n",
                n_fg_cpu, avg_secs, total_works, total_works/avg_secs);
}

struct bench *alloc_bench(int ncpu, int nbg)
{
	struct bench *bench;
	struct worker *worker;
	void *shmem;
	int shmem_size = sizeof(*bench) + sizeof(*worker) * ncpu;
	int i;

	/* alloc shared memory using mmap */
	shmem = mmap(0, shmem_size, PROT_READ | PROT_WRITE, 
		     MAP_SHARED | MAP_ANONYMOUS, -1, 0);
	if (shmem == MAP_FAILED)
		return NULL;
	memset(shmem, 0, shmem_size);

	/* init. */
	bench = (struct bench *)shmem;
	bench->ncpu = ncpu;
	bench->nbg  = nbg;
	bench->workers = (struct worker*)((uint64_t)shmem + sizeof(struct bench));
	for (i = 0; i < ncpu; ++i) {
		worker = &bench->workers[i];
		worker->bench = bench;
		worker->id = i;
		worker->is_bg = i >= (ncpu - nbg);
	}
	return bench;
}

int main(int argc, char *argv[])
{
	struct bench *bench;

	static int nthread;

	if (argc < 2)
		die("usage: %s nthreads", argv[0]);

	nthread = atoi(argv[1]);
	stop = false;

	/* parse command line options */
	bench = alloc_bench(nthread, 0);
	init_bench(bench);
	run_bench(bench);
	report_bench(bench, stdout);

	return 0;
}
