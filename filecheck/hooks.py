def post_init_hook(env):
    # Odoo 17 crons default to numbercall=1 (run once, then deactivate) and the
    # field was removed in Odoo 18: putting it in the XML data would break the
    # 18 install, so the "repeat forever" flag is set here instead.
    cron = env.ref('filecheck.ir_cron_filecheck_sync', raise_if_not_found=False)
    if cron and 'numbercall' in cron._fields:
        cron.numbercall = -1
