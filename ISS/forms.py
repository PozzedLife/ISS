import json
import pytz
import urllib
import urllib2

from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.db import transaction
from django.forms import ValidationError
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from tripphrase import tripphrase

import utils
from models import *

class DurationField(forms.Field):
    def clean(self, value):
        delta = utils.parse_duration(value)

        if not delta:
            raise ValidationError('Invalid duration format.')

        return delta

class BBCodeField(forms.CharField):
    def clean(self, value):
        value = super(BBCodeField, self).clean(value)

        if not isinstance(value, basestring):
            # Probably none, field might be optional, in any case there's no
            # use trying to parse this thing.
            return value

        counts = utils.get_tag_distribution(value)
        embedded_tags = counts['video'] + counts['img'] + counts['bc']
        cool_tags = counts['byusingthistagiaffirmlannyissupercool']
        max_embeds = utils.get_config('max_embedded_items')

        if embedded_tags > max_embeds:
            raise ValidationError(
                ('BBCode must contain %d or fewer embedded items. '
                 'Contained %d.') % (max_embeds, embedded_tags),
                code='TOO_MANY_EMBEDS')

        if cool_tags > 10:
            raise ValidationError(
                'Cool tag bro, but don\'t overuse it.',
                code='TOO_MUCH_COOL')

        return value

class InitialPeriodLimitingForm(forms.Form):
    def __init__(self, *args, **kwargs):
        if 'author' not in kwargs:
            raise ValueError('Must be inited with a author')

        self._author = kwargs['author']
        del kwargs['author']

        super(InitialPeriodLimitingForm, self).__init__(*args, **kwargs)

    def clean(self, *args, **kwargs):
        super(InitialPeriodLimitingForm, self).clean(*args, **kwargs)

        post_count = self._author.post_set.count()
        if post_count < utils.get_config('initial_account_period_total'):
            window_start = timezone.now() - utils.get_config(
                'initial_account_period_width')

            posts_in_window = (self._author
                                   .post_set
                                   .order_by('-created')
                                   .filter(created__gte=window_start)
                                   .count())

            if posts_in_window >= utils.get_config(
                    'initial_account_period_limit'):
                raise ValidationError(
                    ('You\'ve made too many posts on a new account. This '
                     'control will be removed once your account is better '
                     'established.'),
                    code='FLOOD_CONTROL')

class NewThreadForm(InitialPeriodLimitingForm):
    error_css_class = 'in-error'
    thread_min_len = utils.get_config('min_thread_title_chars')
    post_min_len = utils.get_config('min_post_chars')
    post_max_len = utils.get_config('max_post_chars')

    preview_action = forms.CharField(initial='new-thread',
                                     required=False,
                                     widget=forms.HiddenInput())
    title = forms.CharField(label='Title',
                            max_length=1000,
                            min_length=thread_min_len,
                            widget=forms.TextInput(attrs={'autofocus': 'true'}))
    content = BBCodeField(label='Post Body',
                           min_length=post_min_len,
                           max_length=post_max_len,
                           widget=forms.Textarea())
    forum = forms.ModelChoiceField(queryset=Forum.objects.all(),
                                   widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        super(NewThreadForm, self).__init__(*args, **kwargs)

        self.thread = None
        self.post = None

    @transaction.atomic
    def save(self, author, ip_addr):
        self.thread = Thread(
            title=self.cleaned_data['title'],
            forum=self.cleaned_data['forum'],
            author=author)

        self.thread.save()

        self.post = Post(
            thread=self.thread,
            content=self.cleaned_data['content'],
            posted_from=ip_addr,
            author=author)

        self.post.save()

        return self.thread

class NewPostForm(InitialPeriodLimitingForm):
    error_css_class = 'in-error'
    post_min_len = utils.get_config('min_post_chars')
    post_max_len = utils.get_config('max_post_chars')

    preview_action = forms.CharField(initial='new-reply',
                                     required=False,
                                     widget=forms.HiddenInput())
    content = BBCodeField(label='Reply',
                           min_length=post_min_len,
                           max_length=post_max_len,
                           widget=forms.Textarea())

    thread = forms.ModelChoiceField(queryset=Thread.objects.all(),
                                    widget=forms.HiddenInput())

    def clean_thread(self):
        thread = self.cleaned_data['thread']

        if not thread.can_reply():
            raise ValidationError('Can not reply to a locked thread')

        return thread

    def clean(self, *args, **kwargs):
        super(NewPostForm, self).clean(*args, **kwargs)
        
        try:
            last_post = self._author.post_set.order_by('-created')[0]
        except IndexError:
            return self.cleaned_data


        if last_post.content == self.cleaned_data.get('content', ''):
            raise ValidationError('Duplicate of your last post.', code='DUPE')

        return self.cleaned_data

    def get_post(self):
        self.post = Post(
            thread=self.cleaned_data['thread'],
            content=self.cleaned_data['content'],
            author=self._author)

        return self.post

class EditPostForm(forms.Form):
    error_css_class = 'in-error'
    post_min_len = utils.get_config('min_post_chars')
    post_max_len = utils.get_config('max_post_chars')

    preview_action = forms.CharField(initial='edit-post',
                                     required=False,
                                     widget=forms.HiddenInput())
    post = forms.ModelChoiceField(queryset=Post.objects.all(),
                                  widget=forms.HiddenInput())
    
    content = BBCodeField(label='Content',
                          min_length=post_min_len,
                          max_length=post_max_len,
                          widget=forms.Textarea(attrs={'autofocus': 'true'}))

    def save(self, editor, editor_ip=None):
        post = self.cleaned_data['post']

        snapshot = PostSnapshot(
            post=post,
            content=post.content,
            obsolesced_by=editor,
            obsolescing_ip=editor_ip)
        snapshot.save()

        post.content = self.cleaned_data['content']
        post.has_been_edited = True

        post.save()

class StructuralPreviewPostForm(forms.Form):
    error_css_class = 'in-error'
    post_min_len = utils.get_config('min_post_chars')
    post_max_len = utils.get_config('max_post_chars')

    preview_action = forms.ChoiceField(choices=[('new-reply', 'new-reply'),
                                                ('new-thread', 'new-thread'),
                                                ('compose-pm', 'compose-pm'),
                                                ('edit-post', 'edit-post')],
                                       required=True)

    post = forms.ModelChoiceField(queryset=Post.objects.all(),
                                  widget=forms.HiddenInput(),
                                  required=False)
    thread = forms.ModelChoiceField(queryset=Thread.objects.all(),
                                    widget=forms.HiddenInput(),
                                    required=False)
    forum = forms.ModelChoiceField(queryset=Forum.objects.all(),
                                   widget=forms.HiddenInput(),
                                   required=False)

class RenderBBCodeForm(forms.Form):
    error_css_class = 'in-error'
    post_min_len = utils.get_config('min_post_chars')
    post_max_len = utils.get_config('max_post_chars')

    content = BBCodeField(min_length=post_min_len, max_length=post_max_len)

class ThreadActionForm(forms.Form):
    @classmethod
    def _get_action_field(cls):
        choices = [('edit-thread', 'Edit Thread'),
                   ('delete-posts', 'Delete Posts'),
                   ('trash-thread', 'Trash Thread')]

        for forum in Forum.objects.all():
            choices.append(('move-to-%d' % forum.pk,
                            '-> Move to %s' % forum.name))

        return forms.ChoiceField(
            label="",
            required=True,
            choices=choices)

    def __init__(self, *args, **kwargs):
        super(ThreadActionForm, self).__init__(*args, **kwargs)
        self.fields['action'] = self._get_action_field()

class ISSAuthenticationForm(AuthenticationForm):
    def clean_username(self):
        """
        Normalize the submitted username and replace the value in cleaned data
        with it's cannonical form. Do nothing if no user with that username
        (or rather normalized image of their username) exists.
        """
        username = self.cleaned_data['username']
        normalized = Poster.normalize_username(username)

        try:
            user = Poster.objects.get(normalized_username=normalized)
        except Poster.DoesNotExist, e:
            return username
        else:
            return user.username

@utils.captchatize_form
class RegistrationForm(UserCreationForm):
    error_css_class = 'in-error'

    class Meta:
        model = Poster
        fields = ('username', 'email')

    def clean_username(self):
        username = self.cleaned_data['username']
        norm_username = Poster.normalize_username(username)
        forbidden_names = {
            Poster.normalize_username(utils.get_config('junk_user_username')),
            Poster.normalize_username(utils.get_config('system_user_username'))
        }

        if norm_username in forbidden_names:
            raise ValidationError('You may not register that username.',
                                  code='FORBIDDEN_USERNAME')

        if len(norm_username) < 1:
            raise ValidationError('Invalid username', code='INVALID_GENERAL')

        if Poster.objects.filter(normalized_username=norm_username).count():
            raise ValidationError(
                'User with a similar username already exists',
                code='TOO_SIMILAR')

        return username

    def save(self):
        poster = UserCreationForm.save(self)
        return poster

class InviteRegistrationFrom(RegistrationForm):
    registration_code = forms.CharField(label='Registration Code',
                                        max_length=256,
                                        required=True)

    def clean_registration_code(self):
        code = self.cleaned_data['registration_code']
        candidate_codes = RegistrationCode.objects.filter(code=code,
                                                          used_by=None)
        if candidate_codes.count() < 1:
            raise ValidationError('Unrecognized registration code',
                                  code='INVALID_REG_CODE')

        reg_code = candidate_codes[0]

        if timezone.now() > reg_code.expires:
            raise ValidationError('This registration code has already expired.',
                                  code='EXPIRED_REG_CODE')

        return reg_code

    def save(self):
        poster = RegistrationForm.save(self)
        reg_code = self.cleaned_data['registration_code']

        reg_code.used_by = poster
        reg_code.used_on = timezone.now()
        reg_code.save()

        return poster

class InitiatePasswordRecoveryForm(forms.Form):
    username = forms.CharField(label='Username', max_length=1024, required=True)

    def clean(self, *args, **kwargs):
        ret = super(InitiatePasswordRecoveryForm, self).clean(*args, **kwargs)

        username = self.cleaned_data.get('username', None)
        normalized = Poster.normalize_username(username)
        posters = Poster.objects.filter(normalized_username=normalized)

        if posters.count() != 1:
            raise ValidationError('No user with that username exists.',
                                  code='UNKNOWN_EMAIL_ADDR')

        return ret

class ExecutePasswordRecoveryForm(forms.Form):
    password = forms.CharField(label='New Password',
                               min_length=6,
                               required=True,
                               widget=forms.PasswordInput)
    password_repeat = forms.CharField(label='New Password (repeat)',
                               min_length=6,
                               required=True,
                               widget=forms.PasswordInput)
    code = forms.CharField(required=True, widget=forms.HiddenInput)

    def clean(self, *args, **kwargs):
        ret = super(ExecutePasswordRecoveryForm, self).clean(*args, **kwargs)

        if self.cleaned_data.get('password', False):
            password = self.cleaned_data.get('password', None)
            repeat = self.cleaned_data.get('password_repeat', None)

            if password != repeat: 
                raise ValidationError('Passwords didn\'t match',
                                      code='PASSWORD_DIDNT_MATCH')

        return ret
    

@utils.captchatize_form
class ReportPostForm(forms.Form):
    post_min_len = utils.get_config('min_post_chars')
    post_max_len = utils.get_config('max_post_chars')

    post = forms.ModelChoiceField(queryset=Post.objects.all(),
                                  widget=forms.HiddenInput())
    reason = forms.ChoiceField(
        label='Reason for reporting',
        choices=utils.get_config('report_reasons'),
        required=True)

    explanation = BBCodeField(label='Explanation for reporting',
                               min_length=post_min_len,
                               max_length=post_max_len,
                               widget=forms.Textarea())

    def clean_post(self):
        author = self.cleaned_data['post'].author

        if author.is_banned():
            raise ValidationError('This poster has already been banned.',
                                  code='ALREADY_BANNED')

        return self.cleaned_data['post']
    
class UserSettingsForm(forms.Form):
    error_css_class = 'in-error'

    email = forms.EmailField(label="Email address")
    timezone = forms.ChoiceField(
            label="Timezone",
            choices=[(tz, tz) for tz in pytz.common_timezones])
    posts_per_page = forms.IntegerField(
            label="Posts to show per page",
            max_value=50,
            min_value=10)
    new_password = forms.CharField(label='New Password',
                                   min_length=6,
                                   required=False,
                                   widget=forms.PasswordInput)
    new_password_repeat = forms.CharField(label='New Password (repeat)',
                                          required=False,
                                          widget=forms.PasswordInput)
    allow_js = forms.BooleanField(label="Enable javascript", required=False)
    allow_avatars = forms.BooleanField(label="Show user avatars", required=False)
    enable_editor_buttons = forms.BooleanField(
        label="Enable editor buttons",
        required=False)
    allow_image_embed = forms.BooleanField(
        label="Enable images embedded in posts",
        required=False)
    allow_video_embed = forms.BooleanField(
        label="Enable videos embedded in posts",
        required=False)
    enable_tripphrase = forms.BooleanField(label="Enable tripphrase", required=False)
    auto_subscribe = forms.ChoiceField(
        label="Auto-subscribe",
        required=True,
        choices=Poster.SUBSCRIBE_CHOICES,
        widget=forms.RadioSelect)

    def clean(self, *args, **kwargs):
        ret = super(UserSettingsForm, self).clean(*args, **kwargs)

        if self.cleaned_data.get('new_password', False):
            password = self.cleaned_data.get('new_password', None)
            repeat = self.cleaned_data.get('new_password_repeat', None)

            if password != repeat: 
                raise ValidationError('Passwords didn\'t match',
                                      code='PASSWORD_DIDNT_MATCH')

        return ret

    def save(self, poster):
        poster.email = self.cleaned_data['email']
        poster.allow_js = self.cleaned_data['allow_js']
        poster.allow_avatars = self.cleaned_data['allow_avatars']
        poster.allow_image_embed = self.cleaned_data['allow_image_embed']
        poster.allow_video_embed = self.cleaned_data['allow_video_embed']
        poster.enable_editor_buttons = self.cleaned_data['enable_editor_buttons']
        poster.auto_subscribe = self.cleaned_data['auto_subscribe']
        poster.timezone = self.cleaned_data['timezone']
        poster.posts_per_page = self.cleaned_data['posts_per_page']

        if self.cleaned_data['enable_tripphrase']:
            poster.tripphrase = tripphrase(poster.username)
        else:
            poster.tripphrase = None

        if self.cleaned_data['new_password']:
            poster.set_password(self.cleaned_data['new_password'])

        poster.save()

        return poster

class UserAvatarForm(forms.Form):
    error_css_class = 'in-error'

    avatar = forms.ImageField(required=False)

    def clean_avatar(self):
        avatar = self.cleaned_data.get('avatar')

        if not avatar:
            return

        max_size = utils.get_config('max_avatar_size')
        if avatar.size > max_size:
            raise ValidationError(
                'Image muse be less than %d bytes.' % max_size)

        try:
            image = Image.open(avatar)
            height, width = image.size
        except:
            raise ValidationError('Unexpected error intrepreting image.')
        finally:
            if height > 75 or width > 75:
                raise ValidationError(
                    'Image must be less than or equal to 75px in both width '
                    'and height.')

        return avatar

    def save(self, poster, save=True):
        poster.avatar.delete(save=save)
        poster.avatar = self.cleaned_data['avatar']

        return poster

class NewPrivateMessageForm(forms.Form):
    error_css_class = 'in-error'
    post_min_len = utils.get_config('min_post_chars')
    post_max_len = utils.get_config('max_post_chars')
    title_min_len = utils.get_config('min_thread_title_chars')

    preview_action = forms.CharField(initial='compose-pm',
                                     required=False,
                                     widget=forms.HiddenInput())

    subject = forms.CharField(label='Title',
                              max_length=255,
                              min_length=title_min_len)

    to = forms.CharField(
        label='To',
        max_length=512,
        widget=forms.TextInput(attrs={
            'data-auto-suggest': 'true',
            'data-auto-suggest-delimiter': ',',
        }))

    content = BBCodeField(label='Reply',
                          min_length=post_min_len,
                          max_length=post_max_len,
                          widget=forms.Textarea())


    _author = None

    def __init__(self, *args, **kwargs):
        if 'author' not in kwargs:
            raise ValueError('Must be inited with a author')

        # Delay setting this attr because urlconf initilization must be
        # complete for reverse to function
        self.base_fields['to'].widget.attrs['data-auto-suggest-endpoint'] = (
                reverse('api-user-serach'))

        self._author = kwargs['author']
        del kwargs['author']

        super(NewPrivateMessageForm, self).__init__(*args, **kwargs)

    def clean_to(self):
        to_line = self.cleaned_data['to']

        receivers = []
        unfound = []

        for username in to_line.split(','):
            norm = Poster.normalize_username(username)

            try:
                user = Poster.objects.get(normalized_username=norm)
            except Poster.DoesNotExist:
                error = ValidationError(
                    'User with username %(username)s does not exist.',
                    params={'username': username},
                    code='UNKNOWN_USER')

                unfound.append(error)
            else:
                receivers.append(user)

        if unfound:
            raise ValidationError(unfound)
        else:
            return receivers

    def clean(self, *args, **kwargs):
        super(NewPrivateMessageForm, self).clean(*args, **kwargs)
        
        try:
            last_pm = (PrivateMessage.objects
                .filter(sender=self._author)
                .order_by('-created'))[0]

        except IndexError:
            return self.cleaned_data


        time_since = (timezone.now() - last_pm.created).total_seconds()
        flood_control = utils.get_config('private_message_flood_control')

        if time_since < flood_control:
            raise ValidationError(
                ('Flood control has blocked this message from being sent, '
                 'you can send another PM in %(ttp)d seconds.'),
                params={'ttp': flood_control - time_since},
                code='FLOOD_CONTROL')


        return self.cleaned_data

    @transaction.atomic
    def save(self):
        return PrivateMessage.send_pm(
            self._author,
            self.cleaned_data['to'],
            self.cleaned_data['subject'],
            self.cleaned_data['content'])

class SpamCanUserForm(forms.Form):
    poster = forms.ModelChoiceField(queryset=Poster.objects.all(),
                                    widget=forms.HiddenInput())
    target_forum = forms.ModelChoiceField(
        queryset=Forum.objects.filter(is_trash=True),
        required=True,
        empty_label=None)
    next_page = forms.CharField(widget=forms.HiddenInput())

class IssueBanForm(forms.Form):
    _ban_targets = Poster.objects.filter(is_admin=False)
    poster = forms.ModelChoiceField(queryset=_ban_targets,
                                    required=True,
                                    widget=forms.HiddenInput())
    duration = DurationField(required=True)
    reason = forms.CharField(max_length=1024)
