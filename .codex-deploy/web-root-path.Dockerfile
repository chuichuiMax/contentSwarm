ARG SOURCE_IMAGE
FROM ${SOURCE_IMAGE}

# The application is hosted at /, while the published image was built with
# VITE_BASE_PATH=/boyun/. Rewrite only generated frontend text assets so the
# newest application code can run correctly at the production root URL.
RUN find /usr/share/nginx/html -type f \
      \( -name '*.html' -o -name '*.js' -o -name '*.css' \) \
      -exec sed -i 's#/boyun/#/#g' {} +
