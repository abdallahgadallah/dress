from django.apps import AppConfig


class DressesConfig(AppConfig):
    name = "dresses"

    def ready(self):
        # Register iPhone HEIC/HEIF support with Pillow before Django validates uploads.
        from pillow_heif import register_heif_opener

        register_heif_opener()
