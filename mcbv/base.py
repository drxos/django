from __future__ import unicode_literals

import logging
from functools import update_wrapper

from django import http
from django.core.exceptions import ImproperlyConfigured
from django.template.response import TemplateResponse
from django.utils.decorators import classonlymethod
from django.utils import six


logger = logging.getLogger('django.request')


class ContextMixin(object):
    """
    A default context mixin that passes the keyword arguments received by
    get_context_data as the template context.
    """
    def add_context(self):
        """Convenience method; may be overridden to add context by returning a dictionary."""
        return {}

    def get_context_data(self, **kwargs):
        if 'view' not in kwargs:
            kwargs['view'] = self
        kwargs.update(self.add_context())
        return kwargs


class View(object):
    """
    Intentionally simple parent class for all views. Only implements
    dispatch-by-method and simple sanity checking.
    """

    http_method_names = ['get', 'post', 'put', 'delete', 'head', 'options', 'trace']

    def __init__(self, **kwargs):
        """
        Constructor. Called in the URLconf; can contain helpful extra
        keyword arguments, and other things.
        """
        # Go through keyword arguments, and either save their values to our
        # instance, or raise an error.
        for key, value in six.iteritems(kwargs):
            setattr(self, key, value)

    @classonlymethod
    def as_view(cls, **initkwargs):
        """
        Main entry point for a request-response process.
        """
        # sanitize keyword arguments
        for key in initkwargs:
            if key in cls.http_method_names:
                raise TypeError("You tried to pass in the %s method name as a "
                                "keyword argument to %s(). Don't do that."
                                % (key, cls.__name__))
            if not hasattr(cls, key):
                raise TypeError("%s() received an invalid keyword %r. as_view "
                                "only accepts arguments that are already "
                                "attributes of the class." % (cls.__name__, key))

        def view(request, *args, **kwargs):
            self = cls(**initkwargs)
            if hasattr(self, 'get') and not hasattr(self, 'head'):
                self.head = self.get
            self.request = request
            self.user    = request.user
            self.args    = args
            self.kwargs  = kwargs
            return self.dispatch(request, *args, **kwargs)

        # take name and docstring from class
        update_wrapper(view, cls, updated=())

        # and possible attributes set by decorators
        # like csrf_exempt from dispatch
        update_wrapper(view, cls.dispatch, assigned=())
        return view

    def initsetup(self):
        pass

    def dispatch(self, request, *args, **kwargs):
        # Try to dispatch to the right method; if a method doesn't exist,
        # defer to the error handler. Also defer to the error handler if the
        # request method isn't on the approved list.

        if request.method.lower() in self.http_method_names:
            handler = getattr(self, request.method.lower(), self.http_method_not_allowed)
        else:
            handler = self.http_method_not_allowed

        self.initsetup()
        return handler(request, *args, **kwargs)

    def http_method_not_allowed(self, request, *args, **kwargs):
        logger.warning('Method Not Allowed (%s): %s', request.method, request.path,
            extra={
                'status_code': 405,
                'request': self.request
            }
        )
        return http.HttpResponseNotAllowed(self._allowed_methods())

    def options(self, request, *args, **kwargs):
        """
        Handles responding to requests for the OPTIONS HTTP verb.
        """
        response = http.HttpResponse()
        response['Allow'] = ', '.join(self._allowed_methods())
        response['Content-Length'] = '0'
        return response

    def _allowed_methods(self):
        return [m.upper() for m in self.http_method_names if hasattr(self, m)]

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        return self.render_to_response(context)


class TemplateResponseMixin(object):
    """
    A mixin that can be used to render a template.
    """
    template_name = None
    response_class = TemplateResponse

    def render_to_response(self, context, **response_kwargs):
        """
        Returns a response, using the `response_class` for this
        view, with a template rendered with the given context.

        If any keyword arguments are provided, they will be
        passed to the constructor of the response class.
        """
        return self.response_class(
            request = self.request,
            template = self.get_template_names(),
            context = context,
            **response_kwargs
        )

    def get_template_names(self):
        """
        Returns a list of template names to be used for the request. Must return
        a list. May not be called if render_to_response is overridden.
        """
        if self.template_name is None:
            raise ImproperlyConfigured(
                "TemplateResponseMixin requires either a definition of "
                "'template_name' or an implementation of 'get_template_names()'")
        else:
            return [self.template_name]

    def get(self, request, *args, **kwargs):
        from detail import DetailView
        from edit import FormView, FormSetView, CreateView, UpdateView
        from list import ListView

        args    = [request] + list(args)
        context = dict()
        update  = context.update

        if isinstance(self, DetailView)  : update( self.detail_get(*args, **kwargs) )
        if isinstance(self, FormView)    : update( self.form_get(*args, **kwargs) )
        if isinstance(self, FormSetView) : update( self.formset_get(*args, **kwargs) )
        if isinstance(self, CreateView)  : update( self.create_get(*args, **kwargs) )
        if isinstance(self, UpdateView)  : update( self.update_get(*args, **kwargs) )
        if isinstance(self, ListView)    : update( self.list_get(*args, **kwargs) )

        update(self.get_context_data(**kwargs))
        return self.render_to_response(context)


class TemplateView(TemplateResponseMixin, ContextMixin, View):
    """
    A view that renders a template.  This view will also pass into the context
    any keyword arguments passed by the url conf.
    """
    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        return self.render_to_response(context)


class RedirectView(View):
    """
    A view that provides a redirect on any GET request.
    """
    permanent = True
    url = None
    query_string = False

    def get_redirect_url(self, **kwargs):
        """
        Return the URL redirect to. Keyword arguments from the
        URL pattern match generating the redirect request
        are provided as kwargs to this method.
        """
        if self.url:
            url = self.url % kwargs
            args = self.request.META.get('QUERY_STRING', '')
            if args and self.query_string:
                url = "%s?%s" % (url, args)
            return url
        else:
            return None

    def get(self, request, *args, **kwargs):
        url = self.get_redirect_url(**kwargs)
        if url:
            if self.permanent:
                return http.HttpResponsePermanentRedirect(url)
            else:
                return http.HttpResponseRedirect(url)
        else:
            logger.warning('Gone: %s', self.request.path,
                        extra={
                            'status_code': 410,
                            'request': self.request
                        })
            return http.HttpResponseGone()

    def head(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def options(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def delete(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def put(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)
