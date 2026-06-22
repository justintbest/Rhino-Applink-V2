using System.Reflection;
using System.Runtime.InteropServices;
using Rhino.PlugIns;

[assembly: AssemblyTitle("BowlConnector")]
[assembly: AssemblyDescription("Loader plugin so BowlConnector.rui auto-loads at Rhino startup")]
[assembly: AssemblyProduct("BowlConnector")]
[assembly: Guid("CBBEE6AB-5834-4FA7-8294-345EDC31F189")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]

namespace BowlConnector
{
    public class BowlConnectorPlugIn : PlugIn
    {
        public override PlugInLoadTime LoadTime => PlugInLoadTime.AtStartup;
    }
}
