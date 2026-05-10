from django.db import migrations


def migrate_profession_data(apps, schema_editor):
    CustomUser = apps.get_model('api', 'CustomUser')

    mapping = {
        "Étudiant": "Étudiant",
        "Étudiant en informatique": "Étudiant",
        "Lycéen": "Étudiant",
        "Employé de bureau": "Employé",
        "Cadre": "Employé",
        "Manageur": "Employé",
        "Technicien IT": "Ouvrier / Technicien",
        "Ouvrier": "Ouvrier / Technicien",
        "Artisan": "Commerçant / Artisan",
        "Commerçant": "Commerçant / Artisan",
        "Vendeur": "Commerçant / Artisan",
        "Architecte": "Profession libérale",
        "Avocat": "Profession libérale",
        "Comptable": "Profession libérale",
        "Médecin": "Profession libérale",
        "Pharmacien": "Profession libérale",
        "Consultant": "Indépendant / Freelance",
        "Développeur": "Indépendant / Freelance",
        "Chef d'entreprise": "Entrepreneur / Chef d'entreprise",
        "Entrepreneur": "Entrepreneur / Chef d'entreprise",
        "Agriculteur": "Autre",
        "Journaliste": "Autre",
        "Retraité": "Retraité",
        "Sans emploi": "Sans emploi",
    }

    for user in CustomUser.objects.all():
        if user.profession:
            matched = False
            for old_key, new_val in mapping.items():
                if old_key.lower() in user.profession.lower():
                    user.socio_professional_categories = [new_val]
                    matched = True
                    break

            if not matched:
                user.socio_professional_categories = ["Autre"]
        else:
            user.socio_professional_categories = ["Préfère ne pas répondre"]

        # Remplacé par un save simple pour la compatibilité avec les modèles historiques
        user.save()


class Migration(migrations.Migration):
    dependencies = [
        ('api', '0017_customuser_socio_professional_categories'),
    ]

    operations = [
        migrations.RunPython(migrate_profession_data, migrations.RunPython.noop),
    ]