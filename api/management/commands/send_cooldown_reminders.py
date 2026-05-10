from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from django.core.mail import send_mail
from django.conf import settings
from api.models import PurchaseIntention


class Command(BaseCommand):
    help = 'Envoie un e-mail de rappel 2 heures avant la fin de la période de réflexion'

    def handle(self, *args, **kwargs):
        now = timezone.now()
        # On cible les intentions qui expirent dans les 2 prochaines heures
        target_time = now + timedelta(hours=2)

        # Filtrage strict : Période CALM_DOWN, rappel non envoyé, utilisateur consentant
        intentions = PurchaseIntention.objects.filter(
            user_final_decision=PurchaseIntention.DecisionChoices.CALM_DOWN,
            reminder_sent=False,
            cooldown_expires_at__lte=target_time,
            cooldown_expires_at__gte=now,
            user__wants_cooldown_reminders=True
        ).select_related('user')

        count = 0
        for intention in intentions:
            user = intention.user
            subject = "Vrai Besoin - Votre période de réflexion se termine bientôt"

            # Formatage du message
            message = (
                f"Bonjour {user.first_name or ''},\n\n"
                f"Votre période de réflexion pour '{intention.product_name}' "
                f"se termine dans moins de 2 heures.\n\n"
                f"Il est bientôt temps de prendre votre décision finale (Acheter ou Abandonner) "
                f"sur votre tableau de bord.\n\n"
                f"Rendez-vous sur {settings.FRONTEND_URL}/track pour finaliser votre choix.\n\n"
                f"L'équipe Vrai Besoin."
            )

            try:
                # Envoi de l'e-mail via Brevo SMTP (déjà configuré dans vos settings)
                send_mail(
                    subject,
                    message,
                    settings.DEFAULT_FROM_EMAIL,
                    [user.email],
                    fail_silently=False,
                )

                # Marquer comme envoyé pour éviter les doublons
                intention.reminder_sent = True
                intention.save(update_fields=['reminder_sent'])
                count += 1

            except Exception as e:
                self.stderr.write(self.style.ERROR(f"Erreur d'envoi pour {user.email}: {str(e)}"))

        self.stdout.write(self.style.SUCCESS(f"{count} rappel(s) envoyé(s) avec succès."))