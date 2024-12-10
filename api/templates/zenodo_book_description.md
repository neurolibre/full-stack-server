
## About 

{journal_name} Living Preprint built at this {libre_text}, based on the {user_text}. 

❤️ Living preprint: {preprint_server}/{doi_prefix}/{doi_suffix}.{issue_id:05d}

## For the living preprints in JupyterBook format 

You can simply decompress (extract) the zip file and open `index.html` in your browser.

## For the living preprints in MyST format 

If you see the following folders after extracting the zip file, it means that the preprint is in MyST format:

- `site`
- `execute`
- `html`
- `templates`

When you open the `html/index.html` file, you will be able to see the preprint content, however the static webpage components will not be properly loaded. 

This is because the static HTML assets were built with a base URL following the DOI format. As a workaround, you can simply modify the following python script and save it as `serve_preprint.py`:

```python
import http.server
import socketserver
import os

DIRECTORY= "<location-of-the-extracted-zip-file>/LivingPreprint_{doi_prefix}_{doi_suffix}_{journal_name}_{issue_id:05d}_{commit_fork}/html"
BASE_URL = "/{doi_prefix}/{doi_suffix}.{issue_id:05d}"

class CustomHandler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        # Remove the base URL prefix from the path
        if path.startswith(BASE_URL):
            path = path[len(BASE_URL):]
        # Serve files from the specified directory
        path = os.path.join(DIRECTORY, path.lstrip("/"))
        return path

    def do_GET(self):
        # Check if the requested file exists
        file_path = self.translate_path(self.path)
        if not os.path.exists(file_path):
            # If file doesn't exist, try appending `.html`
            file_path += ".html"
            if os.path.exists(file_path):
                # Update the path to point to the .html file
                self.path += ".html"
        # Call the parent class's GET handler
        super().do_GET()

# Set the port for the server
PORT = 8000

with socketserver.TCPServer(("", PORT), CustomHandler) as httpd:
    print(f"Serving at http://localhost:{{PORT}}{{BASE_URL}}")
    httpd.serve_forever()
```

Then you can run the script (`python serve_preprint.py`) and open the given URL in your browser.

Note: The `site` folder contains the living preprint as structured data (in `json` format), which is being used by {journal_name} to serve your publication as a dynamic webpage. For more details, please visit the corresponding [myst documentation](https://mystmd.org/guide/deployment).

{review_text} 
{sign_text}

✉️ info@neurolibre.org