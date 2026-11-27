# Contributing to msol

Thanks for helping! Changes go in through pull requests against `main`.

## Sign off your commits (DCO)

msol uses the [Developer Certificate of Origin](https://developercertificate.org/)
(DCO). By signing off a commit you certify that you wrote it, or otherwise have
the right to submit it under the project's MIT licence.

Sign off with `-s`, which adds a line with your name and email:

```bash
git commit -s -m "Describe the change"
```

```
Signed-off-by: Your Name <you@example.com>
```

The name and email must match the commit's author. The **DCO** check fails a
pull request with commits that aren't signed off. To sign off commits you
already made on your branch:

```bash
git rebase --signoff origin/main
git push --force-with-lease
```

## Checks on pull requests

- **Lint**: shellcheck, ruff and checks on `apps.conf`
- **Build .deb**: builds and test-installs the package
- **CodeQL**: code security analysis
- **SonarQube Cloud**: code quality and security analysis; the check fails, and
  blocks the merge, when the change doesn't pass SonarQube Cloud's quality gate
- **Dependency review**: blocks newly added dependencies with known vulnerabilities
- **DCO**: every commit is signed off

Report security problems privately, as described in [SECURITY.md](SECURITY.md).
