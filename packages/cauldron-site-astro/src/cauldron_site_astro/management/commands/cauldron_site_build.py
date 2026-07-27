"""Management command to build the Cauldron public site using Astro."""
import os

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Build the Cauldron public site using Astro."

    def add_arguments(self, parser):
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Print the Astro build log.",
        )
        parser.add_argument(
            "--worker",
            action="store_true",
            help="Run as a coalescing worker: loop until no more pending builds.",
        )

    def handle(self, *args, **options):
        if options.get("worker"):
            self._run_as_worker(options)
        else:
            self._run_once(options)

    def _run_once(self, options):
        from cauldron_site_astro.service import get_build_service

        verbose = options.get("verbose", False)

        self.stdout.write("Building public site...")
        svc = get_build_service()
        result = svc.build()

        if verbose and result.build_log:
            self.stdout.write(result.build_log)

        if result.ok:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Site built successfully: {result.pages_built} page(s) → {result.output_dir}"
                )
            )
        else:
            self.stderr.write(self.style.ERROR(f"Site build failed: {result.error}"))
            raise SystemExit(1)

    def _run_as_worker(self, options):
        """Worker loop: run build until no pending file remains."""
        from pathlib import Path
        from cauldron_site_astro.config import get_site_astro_config
        from cauldron_site_astro.service import get_build_service

        cfg = get_site_astro_config()
        output_root = Path(cfg.output_root) if cfg.output_root else None
        pid_path = Path(str(output_root) + ".build.pid") if output_root else None
        pending_path = Path(str(output_root) + ".build.pending") if output_root else None

        # Write our PID
        if pid_path:
            try:
                pid_path.parent.mkdir(parents=True, exist_ok=True)
                pid_path.write_text(str(os.getpid()))
            except OSError:
                pass

        try:
            while True:
                # Clear pending BEFORE building so any new dispatch during build is noticed
                if pending_path:
                    try:
                        pending_path.unlink(missing_ok=True)
                    except OSError:
                        pass

                svc = get_build_service()
                result = svc.build()
                if result.ok:
                    self.stdout.write(
                        self.style.SUCCESS(f"[worker] Built {result.pages_built} page(s)")
                    )
                else:
                    self.stderr.write(self.style.ERROR(f"[worker] Build failed: {result.error}"))

                # Check if a new dispatch arrived during the build
                if pending_path and pending_path.exists():
                    continue  # loop again
                break  # no pending: done
        finally:
            if pid_path:
                try:
                    pid_path.unlink(missing_ok=True)
                except OSError:
                    pass
