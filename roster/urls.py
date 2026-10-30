from django.urls import path
from . import views

urlpatterns = [
    path("health/", views.health),
    path("hr/events/", views.BatchView.as_view()),
    path("employees/<str:employee_id>/", views.EmployeeView.as_view()),
    path("employees/<str:employee_id>/transport/", views.TransportView.as_view()),
    path("employees/<str:employee_id>/pickup/", views.TransportView.as_view()),
    path("imports/", views.ImportView.as_view()),
    path("imports/<int:run_id>/", views.ImportDetailView.as_view()),
    path("imports/<int:run_id>/apply/", views.ImportApplyView.as_view()),
    path("internal/events/", views.internal_event),
    path("metrics/", views.MetricsView.as_view()),
]
