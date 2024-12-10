## About 

Docker image built from the {libre_text}, based on the {user_text}, using repo2docker (through BinderHub). 

❤️ Living preprint: {preprint_server}/{doi_prefix}/{doi_suffix}.{issue_id:05d}

<br> To run locally: <ol> <li><pre><code class=\"language-bash\">docker load < DockerImage_{doi_prefix}_{journal_name}_{issue_id:05d}_{commit_fork}.tar.gz</code><pre></li><li><pre><code class=\"language-bash\">docker run -it --rm -p 8888:8888 DOCKER_IMAGE_ID jupyter lab --ip 0.0.0.0</code></pre> </li></ol> <p><strong>by replacing <code>DOCKER_IMAGE_ID</code> above with the respective ID of the Docker image loaded from the zip file.</strong></p> 

{review_text} 
{sign_text}

✉️ info@neurolibre.org