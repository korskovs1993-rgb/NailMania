import re
from django import forms
from django.core.exceptions import ValidationError
from .models import Client, Service, Studio, DayOverride, PersonalBlock, PersonalTemplate

TIME_CHOICES = [('', 'Выбери время')] + [(f'{h:02}:{m:02}', f'{h:02}:{m:02}') for h in range(24) for m in (0, 15, 30, 45)]


class QuarterTimeField(forms.TimeField):
    widget = forms.Select

    def __init__(self, *args, **kwargs):
        kwargs['widget'] = forms.Select(choices=TIME_CHOICES)
        super().__init__(*args, **kwargs)

    def validate(self, value):
        super().validate(value)
        if value and (value.minute % 15 or value.second or value.microsecond):
            raise ValidationError('Выбери время с шагом 15 минут.')


class QuarterDateTimeField(forms.SplitDateTimeField):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields = (self.fields[0], QuarterTimeField())
        self.widget = forms.MultiWidget(widgets=[forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}), forms.Select(choices=TIME_CHOICES)])
        self.widget.decompress = forms.SplitDateTimeWidget().decompress


def normalize_phone(value):
    digits = re.sub(r'[^0-9]', '', value)
    if len(digits) == 11 and digits[0] in '78':
        return '+7' + digits[1:]
    raise ValidationError('Укажи российский номер: +7 и ещё 10 цифр.')


class BookingForm(forms.Form):
    services = forms.ModelMultipleChoiceField(label='Услуги', queryset=Service.objects.filter(active=True), widget=forms.CheckboxSelectMultiple)
    date = forms.DateField(label='Дата', widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}))
    time = QuarterTimeField(label='Начало')
    name = forms.CharField(label='Твоё имя', max_length=100, widget=forms.TextInput(attrs={'autocomplete': 'given-name'}))
    phone = forms.CharField(label='Телефон', max_length=32, widget=forms.TextInput(attrs={'type': 'tel', 'autocomplete': 'tel', 'placeholder': '+7 999 123-45-67'}))
    comment = forms.CharField(label='Комментарий, если нужен', required=False, max_length=1000, widget=forms.Textarea(attrs={'rows': 2}))
    consent = forms.BooleanField(label='Даю отдельное согласие на обработку персональных данных')
    website = forms.CharField(required=False, widget=forms.HiddenInput)
    def clean_phone(self):
        return normalize_phone(self.cleaned_data['phone'])
    def clean_website(self):
        if self.cleaned_data['website']:
            raise ValidationError('Не удалось отправить форму.')
        return ''
    def clean(self):
        data = super().clean()
        if data.get('date') and data.get('time'):
            from django.utils import timezone
            from datetime import datetime
            data['start'] = timezone.make_aware(datetime.combine(data['date'], data['time']))
        return data


class ClientChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f'{obj.name} · {obj.phone}'


class StaffBookingForm(BookingForm):
    client = ClientChoiceField(label='Клиентка из базы', queryset=Client.objects.all(), required=False, empty_label='Первичная запись — новая клиентка')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['name'].required = False
        self.fields['phone'].required = False
        self.fields['name'].label = 'Имя новой клиентки'
        self.order_fields(['client', 'services', 'date', 'time', 'name', 'phone', 'comment', 'consent', 'website'])

    def clean_phone(self):
        value = self.cleaned_data.get('phone')
        return normalize_phone(value) if value and not self.cleaned_data.get('client') else ''

    def clean(self):
        data = super().clean()
        client = data.get('client')
        if client:
            data['name'], data['phone'] = client.name, client.phone
        else:
            for field in ('name', 'phone'):
                if not data.get(field):
                    self.add_error(field, 'Заполни для новой клиентки или выбери клиентку из базы.')
        return data


class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ['name', 'phone', 'notes']
        widgets = {'notes': forms.Textarea(attrs={'rows': 4}), 'phone': forms.TextInput(attrs={'type': 'tel'})}
    def clean_phone(self):
        return normalize_phone(self.cleaned_data['phone'])


class BlockForm(forms.ModelForm):
    start = QuarterDateTimeField(label='Начало')
    end = QuarterDateTimeField(label='Окончание')
    class Meta:
        model = PersonalBlock
        fields = ['title', 'start', 'end']
        widgets = {x: forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local', 'step': '900'}) for x in ('start', 'end')}
    def clean(self):
        data = super().clean()
        if data.get('start') and data.get('end') and data['end'] <= data['start']:
            raise ValidationError('Окончание должно быть позже начала.')
        return data


class PersonalTemplateForm(forms.ModelForm):
    class Meta:
        model = PersonalTemplate
        fields = ['title', 'minutes', 'icon']
        labels = {'title': 'Название', 'minutes': 'Длительность'}
        widgets = {'minutes': forms.Select(choices=[(m, f'{m // 60} ч {m % 60:02} мин') for m in range(15, 1441, 15)])}

    def clean_minutes(self):
        value = self.cleaned_data['minutes']
        if value % 15:
            raise ValidationError('Длительность должна быть кратна 15 минутам.')
        return value


class StudioForm(forms.ModelForm):
    class Meta:
        model = Studio
        fields = ['name', 'city', 'horizon', 'lead_minutes']
        labels = {'name': 'Имя мастера', 'city': 'Город', 'step': 'Шаг начала записи, мин', 'horizon': 'Запись вперёд, дней', 'lead_minutes': 'Минимум до начала, мин'}


class DayForm(forms.ModelForm):
    start = QuarterTimeField(label='Начало дня')
    end = QuarterTimeField(label='Конец дня')
    class Meta:
        model = DayOverride
        fields = ['date', 'closed', 'start', 'end']
        labels = {'date': 'Дата', 'closed': 'Выходной', 'start': 'Начало дня', 'end': 'Конец дня'}
        widgets = {'date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}), 'start': forms.TimeInput(format='%H:%M', attrs={'type': 'time'}), 'end': forms.TimeInput(format='%H:%M', attrs={'type': 'time'})}
    def clean(self):
        data = super().clean()
        if not data.get('closed') and data.get('start') and data.get('end') and data['end'] <= data['start']:
            raise ValidationError('Конец дня должен быть позже начала.')
        return data


class ServiceForm(forms.ModelForm):
    class Meta:
        model = Service
        fields = ['name', 'minutes', 'active']
        labels = {'minutes': 'Длительность', 'active': 'Доступна клиенткам'}
        widgets = {'minutes': forms.Select(choices=[(m, f'{m // 60} ч {m % 60:02} мин') for m in range(15, 481, 15)])}
