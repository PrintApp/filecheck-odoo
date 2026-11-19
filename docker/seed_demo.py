# Local demo seed (placeholder keys, not real ones):
#   docker compose run --rm -T odoo18 odoo shell -d demo18 --no-http < seed_demo.py
site = env.ref('website.default_website')
site.with_context(filecheck_skip_sync=True).write({
    'filecheck_publishable_key': 'pk_demo_placeholder',
    'filecheck_secret_key': 'sk_00000000000000000000000000000000',
})
Res = env['filecheck.resource']
wf = Res.search([('filecheck_id', '=', 'wf_demo')]) or Res.create({
    'name': 'Demo workflow', 'filecheck_id': 'wf_demo', 'kind': 'workflow', 'website_id': site.id})
site.filecheck_default_workflow_id = wf
tmpl = env['product.template'].search([('sale_ok', '=', True), ('website_published', '=', True)], limit=1)
tmpl.filecheck_mode = 'default'
env.cr.commit()
print('SEEDED', tmpl.id, tmpl.website_url)
