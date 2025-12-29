from django.urls import path
from . import views
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('', views.home, name='home'),  # Home page
    path('login/', views.login_view, name='login'),  # Login page
    path('register/', views.register_view, name='register'),  # Registration page
    path('logout/', views.home, name='logout'),
    path('medico/home/', views.medico_home, name='medico_home'),
    path('medico/analisi/', views.analisi_paziente, name='analisi_paziente'),
    path('medico/riassunto/', views.riassunto_caso_clinico, name='riassunto_caso_clinico'),
    path('paziente/home/', views.paziente_home, name='paziente_home'),
    path('medico/note/<int:nota_id>/modifica/', views.modifica_testo_medico, name='modifica_testo_medico'),
    path('medico/personalizza/', views.personalizza_generazione, name='personalizza_generazione'),
    path('paziente/note/<int:nota_id>/elimina/', views.elimina_nota, name='elimina_nota'),
    path('medico/rigenera_frase_clinica/', views.rigenera_frase_clinica, name='rigenera_frase_clinica'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
