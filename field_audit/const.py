
# Number of records to bulk fetch/insert per batch for bootstrap operations.
#
# Benchmark testing of this value was performed by bootstrapping a table with
# five (5) columns and ~2.6 million rows. Two benchmark runs were performed with
# the database reset and restarted between runs. The first benchmark used
# 'batch_size=1000' and completed in 6min 15sec, the second benchmark used
# 'batch_size=10000' and completed in 6min 12sec (less than 1% difference in
# runtime). There was no noticeable difference in database resource usage
# between the two runs, but the Django process consistently used about 160MiB
# more memory for the duration of the second benchmark compared to the first.
# Given that the overall runtime was relatively unaffected between two tests
# whose batch size differed by an order of magnitude, the lower value seems like
# the better default due to the lower Django resource usage.
#
# Installations with noticeable database connection latency may prefer to
# specify a higher value on their bootstrap operations in order to optimize for
# fewer round-trips to the database.
BOOTSTRAP_BATCH_SIZE = 1000
