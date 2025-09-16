---
title: 'EU Air Traffic: an open platform for live and historical European airspace analytics'
tags:
  - Python
  - aviation
  - ADS-B
  - data engineering
  - emissions
  - air transport
authors:
  - name: Swadhin Biswas
    orcid: 0009-0005-2980-6651
    affiliation: 1
affiliations:
 - name: Independent Researcher, Bangladesh
   index: 1
date: 15 September 2026
bibliography: paper.bib
---

# Summary

EU Air Traffic collects live and scheduled flight data over Europe and stores
it as an open data lake. A small collector polls aircraft positions, flight
movements, schedules, weather, and reference tables, publishes the records to
Kafka, and a scheduled pipeline turns the stream into versioned Bronze, Silver,
and Gold layers with dbt models and tests. The same repository ships a fuel and
emissions estimator built on a precomputed OpenAP grid, a monthly Eurostat
benchmark of official passenger numbers, and a browser dashboard backed by a
read-only serving copy. The whole system runs on free infrastructure tiers,
with quota and budget handling built into the design.

# Statement of need

Anyone studying European air traffic starts from scattered pieces: a movement
feed here, schedules behind an API key there, weather somewhere else, official
statistics in yet another format. Each source has its own credentials, rate
limits, field names, and gaps. Students and researchers who want a composed
picture end up writing the same glue code privately, and the result is rarely
published as reusable software.

This project is that glue, maintained as a system instead of a script. It is
for people who need European flight movements with delays, live positions with
emissions estimates, and airport-level aggregates they can query or download.
The pipeline deduplicates movements seen by several providers, keeps unknown
delays as missing values instead of zeros, and checks every layer with tests
before publishing.

# State of the field

The data this system stands on is open. The OpenSky Network publishes the
reference API for community flight tracking data [@opensky-api]. Eurostat
publishes monthly passenger figures per airport, which this project fetches as
an independent benchmark [@eurostat-avia-paoa]. Schedules and delay fields come
from AirLabs [@airlabs-schedules], and the emissions grid is precomputed from
the OpenAP performance handbook [@openap-handbook].

Those are providers and libraries, not platforms. We are not aware of an open,
end-to-end equivalent that takes these feeds from collection to a served
warehouse with scheduled refreshes, quota-aware polling, and tested analytical
models in one repository. The contribution here is the composition: the
collectors, the lake, the models, and the serving layer are designed to run
unattended on free tiers, and the repository documents each budget it respects.

# Software design

The runtime has two halves. A collector on a small server polls each source on
its own interval, enriches positions with emissions and classification, and
publishes keyed records to a five-topic Kafka cluster, multiplexing related
datasets with a `_kind` discriminator. GitHub Actions drains the bus every 15
minutes, appends Bronze Parquet, merges incremental Silver tables, builds a
DuckDB star schema, and runs the dbt project. Results land in a Hugging Face
dataset, a MotherDuck warehouse, and a Turso serving copy that the dashboard
reads with a read-only token.

Two decisions carry most of the design. The first is that nothing the pipeline
writes can silently vanish: Silver merges are additive, serving tables swap
atomically, and growing tables resume from watermarks. Any step that reports
success without writing fails the run instead. The second is that every
external budget is enforced in code. OpenSky requests are counted against the
documented credit buckets. The schedules fetcher stops at a persisted monthly
call cap. Serving tables are republished only when a content hash changes.
\autoref{fig:architecture} shows the full path from data collection to the
browser.

![Architecture of the EU Air Traffic platform, from collection through Kafka to the lake, warehouse, and dashboard. \label{fig:architecture}](eu-air-traffic.png)

# Research impact statement

The author operates the platform as a public dashboard and publishes the lake
as a versioned dataset. The pipeline feeds the author's own traffic, delay,
and emissions analyses. There are no external publications using the software
yet, so this statement records current use rather than claiming adoption. The
repository is public with tests and continuous integration. The dataset and
dashboard give other groups a ready-made starting point for air-transport
work, which is where wider use would come from.

# AI usage disclosure

An AI coding assistant (Muse Spark) was used during development for
implementation, refactoring, tests, documentation, and drafting of this
manuscript. The author reviewed, edited, and validated all AI-assisted outputs
and made the core architectural and design decisions. The author takes full
responsibility for the accuracy and licensing of the software and this paper.

# Acknowledgements

The author thanks the maintainers of the open data providers this system
builds on, and the open-source projects it depends on.

# References
