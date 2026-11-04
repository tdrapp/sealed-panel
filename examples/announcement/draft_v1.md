Subject: Remote build cache is ready for your services

Hi everyone,

Since 4 November the remote build cache is available to every service in the monorepo. On the three pilot services, median pull request CI time fell from 14 minutes to 6, and weekly CI minutes fell by 38 percent.

Turning it on is one line in your service build config (cache: remote) and needs no change to your tests. If a cached result ever looks wrong, the flag --no-remote-cache restores the old behaviour for one build.

We would like each team to enable it on one service before 22 November, so we can size the cache for the rest of the quarter. Reply with the service name, or book fifteen minutes with us if you want help.

Platform tooling
