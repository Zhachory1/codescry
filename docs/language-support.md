# Language support

| Language / file type | Support |
| --- | --- |
| Python | Parser-backed function/class/method symbol chunks. |
| JavaScript / TypeScript / Go / Java / C# / Ruby / shell and others | Regex-backed symbol hints plus line-window chunks. |
| Markdown / JSON / YAML / TOML / SQL | Line-window chunks. |
| Lockfiles, generated/vendor dirs, binaries, large blobs | Skipped by default. |
| Dirty working tree | Reported in status/results, not indexed. |

Quality varies by language. Add pilot misses to the golden eval set before changing ranking or parsers.
