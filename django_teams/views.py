from django.core.exceptions import PermissionDenied
# This is where your views go :)
from django.http import HttpResponseRedirect
from django.views.generic.list import ListView
from django.views.generic.detail import DetailView
from django.views.generic.edit import CreateView, UpdateView
from django.core.urlresolvers import reverse
from django.shortcuts import get_object_or_404
from django.db.models import Count
from django.contrib.contenttypes.models import ContentType

from django_teams.models import Team, TeamStatus, Ownership
from django_teams.forms import (TeamEditForm,
                                TeamStatusCreateForm,
                                action_formset)

from django.db.models import Case, When
from django.db import models


def loadGenericKeyRelations(queryset):
    content_types = queryset.distinct('content_type').only('content_type')
    object_items = {}
    for content_type in content_types:
        print content_type
        objects = content_type.model_class().filter(queryset.filter(content_type=content_type))
        for relation in content_type.model_class()._meta.get_fields():
            objects.select_related(relation)
        object_items.add(objects)
    return object_items


class TeamListView(ListView):
    model = Team

    def get_queryset(self):
        queryset = Team.objects.all().annotate(member_count=Count('users'))
        queryset = queryset.annotate(role=Case(When(teamstatus__user=self.request.user, then='teamstatus__role'), default=0, outputfield=models.IntegerField()))
        queryset = queryset.annotate(owner=Case(When(teamstatus__role=20, then='users__username'), default=None))
        queryset = queryset.order_by('-role')
        print queryset.all()
        return queryset


class UserTeamListView(ListView):
    template_name = 'django_teams/user_team_list.html'

    def get_queryset(self):
        statuses = TeamStatus.objects.filter(user=self.request.user, role=20)
        return [status.team for status in statuses]


class TeamCreateView(CreateView):
    model = Team
    template_name = 'django_teams/team_create.html'
    fields = ['name', 'description', 'private']

    def dispatch(self, request, *args, **kwargs):
        self.request = request
        return super(TeamCreateView, self).dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        ret = super(TeamCreateView, self).form_valid(form)
        self.object.add_user(self.request.user, team_role=20)
        return ret


class TeamDetailView(DetailView):
    model = Team

    def dispatch(self, request, *args, **kwargs):
        self.request = request
        return super(TeamDetailView, self).dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        object = super(TeamDetailView, self).get_object(queryset)
        if object.private and self.request.user not in object.users.filter(teamstatus__role__gte=10):
            raise PermissionDenied()
        return object

    def render_to_response(self, context, **response_kwargs):
        team = self.object
        context['owners'] = team.users.filter(teamstatus__role=20)
        context['members'] = team.users.filter(teamstatus__role=10)
        owned = Ownership.objects.filter(team=team, approved=True)
        context['approved_objects']  = loadGenericKeyRelations(owned)
        return super(TeamDetailView, self).render_to_response(context, **response_kwargs)


class TeamInfoEditView(UpdateView):
    model = Team
    fields = ['name', 'description', 'private']
    template_name = 'django_teams/teaminfo_form.html'

    def get_object(self, queryset=None):
        object = super(TeamInfoEditView, self).get_object(queryset)
        if self.request.user not in object.users.filter(teamstatus__role__gte=20):
            raise PermissionDenied()
        return object


class TeamEditView(UpdateView):
    model = Team
    form_class = TeamEditForm

    def dispatch(self, request, *args, **kwargs):
        self.request = request
        return super(TeamEditView, self).dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        object = super(TeamEditView, self).get_object(queryset)

        # User must be admin of the object to get into this view
        if self.request.user not in object.users.filter(teamstatus__role__gte=20):
            raise PermissionDenied()
        return object

    def get_form_class(self):
        # get forms for team leaders, team members, team requests
        ret = []
        users = self.object.users
        ret += [action_formset(users.filter(teamstatus__role=20), ('---', 'Demote', 'Remove'))]
        ret += [action_formset(users.filter(teamstatus__role=10), ('---', 'Promote', 'Remove'))]
        ret += [action_formset(users.filter(teamstatus__role=1), ('---', 'Approve', 'Reject'))]
        owned_objects  = Ownership.objects.filter(team=self.object).order_by('-content_type').prefetch_related('content_object')
        ret += [action_formset(owned_objects.filter(approved=True), ('---', 'Remove'), link=True)]
        ret += [action_formset(owned_objects.filter(approved=False), ('---', 'Approve', 'Reject'), link=True)]
        return ret

    def get_form(self, form_class=TeamEditForm):
        kwargs = self.get_form_kwargs()
        form_class = self.get_form_class()

        if 'data' in kwargs:
            ret = [form_class[0](kwargs['data'], prefix='teachers'),
                   form_class[1](kwargs['data'], prefix='students'),
                   form_class[2](kwargs['data'], prefix='member-requests'),
                   form_class[3](kwargs['data'], prefix='approved-objects'),
                   form_class[4](kwargs['data'], prefix='approval-requests')]
        else:
            ret = [form_class[0](prefix='teachers'),
                   form_class[1](prefix='students'),
                   form_class[2](prefix='member-requests'),
                   form_class[3](prefix='approved-objects'),
                   form_class[4](prefix='approval-requests')]

        return ret

    def post(self, request, *args, **kwargs):
        """
        Handles POST requests, instantiating a form instance with the passed
        POST variables and then checked for validity.
        """
        self.object = get_object_or_404(Team, pk=kwargs['pk'])
        form_class = self.get_form_class()
        form = self.get_form(form_class)

        for f in form:
            if not f.is_valid():
                return self.form_invalid(form)

        # Go through each form and perform the action
        # Owners
        owner_action = form[0].cleaned_data['action']
        owner_items = form[0].cleaned_data['items']
        if owner_action == 'Demote':
            for i in owner_items:
                o = self.object.get_user_status(i)
                o.role = 10
                o.save()
        if owner_action == 'Remove':
            for i in owner_items:
                self.object.get_user_status(i).delete()

        # Members
        member_action = form[1].cleaned_data['action']
        member_items = form[1].cleaned_data['items']
        if member_action == 'Promote':
            for i in member_items:
                o = self.object.get_user_status(i)
                o.role = 20
                o.save()
        if member_action == 'Remove':
            for i in member_items:
                self.object.get_user_status(i).delete()

        # Member Requests
        request_action = form[2].cleaned_data['action']
        request_items = form[2].cleaned_data['items']
        if request_action == 'Approve':
            for i in request_items:
                i.role = 10
                i.save()
        if request_action == 'Revoke':
            for i in request_items:
                i.delete()

        current_action = form[3].cleaned_data['action']
        current_items = form[3].cleaned_data['items']
        if current_action == 'Remove':
            for i in current_items:
                i.delete()

        object_action = form[4].cleaned_data['action']
        object_items = form[4].cleaned_data['items']
        if object_action == 'Approve':
            for i in object_items:
                i.approved = True
                i.save()
        if object_action == 'Reject':
            for i in object_items:
                i.delete()

        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse('team-edit', kwargs={'pk': self.object.pk})


class TeamStatusCreateView(CreateView):
    model = TeamStatus
    form_class = TeamStatusCreateForm

    def get_success_url(self):
        return reverse('team-list')

    def dispatch(self, request, *args, **kwargs):
        self.team = Team.objects.get(pk=kwargs['team_pk'])
        self.request = request
        return super(TeamStatusCreateView, self).dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.team = self.team
        form.instance.user = self.request.user
        form.instance.role = 1
        return super(TeamStatusCreateView, self).form_valid(form)

    def render_to_response(self, context, **response_kwargs):
        context['team'] = self.team
        return super(TeamStatusCreateView, self).render_to_response(context, **response_kwargs)
