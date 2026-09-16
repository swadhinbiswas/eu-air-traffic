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

EU Air Traffic [@eu-air-traffic] collects live and scheduled flight data over
Europe and keeps it in an open data lake. A collector runs on a small server and polls aircraft
positions, flight movements, schedules, weather and reference tables; it
publishes every record to Kafka, and a scheduled job turns that stream into
Bronze, Silver and Gold layers with dbt models and tests. The repository also
carries a fuel and emissions estimator built on a precomputed OpenAP grid, a
monthly Eurostat benchmark of official passenger numbers, and a browser
dashboard that reads a read-only serving copy. Everything runs on free
infrastructure tiers, and the quotas those tiers impose are enforced in code.

# Statement of need

Anyone studying European air traffic starts from scattered pieces. A movement
feed here, schedules behind an API key there, weather somewhere else, official
statistics in yet another format. Each source brings its own credentials, rate
limits, field names and gaps, so people who want a composed picture write the
same glue code privately, and that glue rarely ends up published as reusable
software.

This project is that glue maintained in public, so the next person does not
have to write it again. It is for anyone who needs European flight movements
with delays, live positions with emissions estimates, and airport-level
aggregates they can query or download. Movements reported by more than one
provider are deduplicated. A delay that is unknown stays missing, so it never
counts as an on-time flight. Every layer is checked by tests before it goes
out.

# State of the field

The data behind this system is open. OpenSky publishes the reference API for
community flight tracking [@opensky-api]. Eurostat publishes monthly passenger
figures for each airport, which this project fetches as an independent
benchmark [@eurostat-avia-paoa]. Schedules and delay fields come from AirLabs
[@airlabs-schedules], and the emissions grid is precomputed from the OpenAP
performance handbook [@openap-handbook].

Each of those is a provider or a library. I did not find an open, end-to-end
equivalent that carries these feeds from collection through a served warehouse
in one repository, with scheduled refreshes, quota-aware polling and tested
analytical models. The contribution here is the composition. The collectors,
the lake, the models and the serving layer are built to run unattended on free
tiers, and the repository documents every budget it respects.

# Software design

The runtime has two halves. A collector on a small server polls each source on
its own interval, adds emissions and classification to positions, and publishes
keyed records to Kafka. Five topics cover the whole feed; related datasets
share a topic and carry a `_kind` field that the sink uses to split them out
again. GitHub Actions drains the bus every 15 minutes, appends Bronze Parquet,
merges the incremental Silver tables, builds a DuckDB star schema and runs the
dbt project. Results land in a Hugging Face dataset, a MotherDuck warehouse,
and a Turso serving copy that the dashboard reads with a read-only token.

Two properties matter most. Nothing the pipeline writes can disappear quietly:
Silver merges are additive, serving tables swap atomically, and growing tables
resume from watermarks, so a step that reports success without writing fails
the run instead. The other is that each external budget is enforced in code.
OpenSky requests are counted against the documented credit buckets, the
schedules fetcher stops at a persisted monthly call cap, and serving tables are
republished only when a content hash changes. \autoref{fig:architecture} shows
the path from collection to the browser.

![Architecture of the EU Air Traffic platform, from collection through Kafka to the lake, warehouse, and dashboard. \label{fig:architecture}](eu-air-traffic.png)

# Research impact statement

I run the platform as a public dashboard and publish the lake as a versioned
dataset, and the pipeline feeds my own traffic, delay and emissions analyses.
No external publications use the software yet; this section records current use
only. The repository is public with tests and continuous integration, and the
dataset and dashboard give another group a working starting point for
air-transport work.

# AI usage disclosure

Parts of the code, tests and documentation were drafted with an AI coding
assistant, and the same assistant helped draft this manuscript. The author
reviewed, edited and validated all AI-assisted output and made the architecture
and design decisions. Responsibility for the software and this paper rests with
the author.

# Acknowledgements

Thanks to the maintainers of the open data providers this system builds on, and
to the open-source projects it depends on.

# References
