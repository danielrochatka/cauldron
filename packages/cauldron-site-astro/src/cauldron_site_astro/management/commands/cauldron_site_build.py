"""Management command to build the Cauldron public site using Astro."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Build the Cauldron public site using Astro."

    def add_arguments(self, parser):
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Print the Astro build log.",
        )

    def handle(self, *args, **options):
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
