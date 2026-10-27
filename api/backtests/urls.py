from django.urls import path

from backtests import views

urlpatterns = [
    path("catalog", views.Catalog.as_view()),
    path("projects", views.Projects.as_view()),
    path("projects/<int:pk>", views.ProjectDetail.as_view()),
    path("projects/<int:pk>/trials", views.ProjectTrials.as_view()),
    path("projects/<int:pk>/exam", views.Exam.as_view()),
    path("trials/<int:pk>", views.TrialDetail.as_view()),
    path("bars", views.BarsWindow.as_view()),
]
