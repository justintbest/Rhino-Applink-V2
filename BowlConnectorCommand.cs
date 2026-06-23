using System;
using Rhino;
using Rhino.Commands;

namespace BowlConnector
{
    public class BowlConnectorCommand : Command
    {
        public override string EnglishName => "BowlConnector";

        protected override Result RunCommand(RhinoDoc doc, RunMode mode)
        {
            const string script =
                "import glob,os; p=sorted(glob.glob(os.path.join(os.path.expandvars('%APPDATA%'),'McNeel','Rhinoceros','packages','8.0','bowl-connector-1.0.0.1','*','BowlConnector','rhino_api_sender.py'))); exec compile(open(p[-1]).read(), p[-1], 'exec') in {'__name__': '__main__'}";

            EventHandler handler = null;
            handler = (sender, e) =>
            {
                RhinoApp.Idle -= handler;
                RhinoApp.RunScript("! _-RunPythonScript (" + script + ")", false);
            };
            RhinoApp.Idle += handler;

            return Result.Success;
        }
    }
}
